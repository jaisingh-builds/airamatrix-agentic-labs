#!/usr/bin/env python3
"""
aira-ops — the internal operations API every Day 3 lab talks to.

A small, real HTTP service: support tickets, customer accounts, slide-analysis
jobs and a configuration store, in SQLite. Standard library only.

    python3 aira_ops.py                 # http://127.0.0.1:8150
    AIRA_OPS_TOKEN=... python3 aira_ops.py --port 8150 --reset

It behaves like the internal systems agents get wired into at work:

* Every request needs a bearer token (AIRA_OPS_TOKEN). The token lives in the
  environment, never in code - the Day 3 checkpoint asks for exactly that.
* Writes take an Idempotency-Key header. Replaying the same key returns the
  original result instead of creating a duplicate: an agent that retries after
  a timeout must not open two tickets.
* Config writes use optimistic concurrency (expected_version). A stale write
  gets 409, not a silent overwrite.
* Errors share one contract an agent can act on:
      {"error": {"code", "message", "retryable", "hint"}}
* Every write lands in an audit log.

Two ways to authenticate, and the difference is the lesson:

* The shared AIRA_OPS_TOKEN. Anyone holding it is the same caller, so the
  X-Actor header is only a label the caller chose. The audit log says so:
  verified = 0.
* Per-caller tokens (--callers callers.json). Each caller gets its own token;
  the file stores only its SHA-256, plus the caller's actor name, the accounts
  it may see and whether it may write. The actor then comes from the token,
  not the header (verified = 1), and a caller scoped to ACC-1001 gets 404 for
  anything belonging to ACC-1003.

      python3 aira_ops.py --callers callers.json --issue-token triage-agent \
          --accounts ACC-1001            # prints the token once; add --write
"""
import argparse, hashlib, json, os, re, sqlite3, threading, time, uuid
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
DB_LOCK = threading.Lock()
TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
TRANSITIONS = {  # allowed status changes; anything else is 409
    "open": {"in_progress", "closed"},
    "in_progress": {"resolved", "open"},
    "resolved": {"closed", "open"},
    "closed": set(),
}
PRIORITIES = ("P1", "P2", "P3", "P4")

def now():
    return datetime.now(IST).isoformat(timespec="seconds")

# --------------------------------------------------------------------------- db
SCHEMA = """
create table if not exists accounts(id text primary key, name text, tier text,
    region text, contract_sla_minutes integer);
create table if not exists tickets(id text primary key, title text, body text,
    status text, priority text, account_id text, assignee text,
    created_at text, updated_at text);
create table if not exists comments(id integer primary key autoincrement,
    ticket_id text, author text, body text, created_at text);
create table if not exists jobs(id text primary key, account_id text,
    slide_count integer, priority text, status text, submitted_at text);
create table if not exists config(key text primary key, value text,
    version integer, description text, updated_at text);
create table if not exists idem(scope text, key text, fingerprint text,
    method text, path text, status integer, body text, created_at text,
    primary key(scope, key));
create table if not exists audit(id integer primary key autoincrement, at text,
    actor text, action text, target text, detail text);
"""

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

def load_callers(path):
    """callers.json -> {token_sha256: identity}. The file holds hashes, never tokens."""
    if not path or not Path(path).exists():
        return {}
    out = {}
    for c in json.loads(Path(path).read_text()).get("callers", []):
        out[c["token_sha256"]] = {"actor": c["actor"], "accounts": c.get("accounts", "*"),
                                  "write": bool(c.get("write", False)), "verified": True}
    return out

def connect(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("pragma table_info(audit)")}
    if "verified" not in cols:   # databases created before verified identities existed
        db.execute("alter table audit add column verified integer default 0")
    return db

def seed(db):
    data = json.loads((HERE / "seed.json").read_text())
    for a in data["accounts"]:
        db.execute("insert into accounts values(?,?,?,?,?)",
                   (a["id"], a["name"], a["tier"], a["region"], a["contract_sla_minutes"]))
    for t in data["tickets"]:
        db.execute("insert into tickets values(?,?,?,?,?,?,?,?,?)",
                   (t["id"], t["title"], t["body"], t["status"], t["priority"],
                    t["account_id"], t.get("assignee"), t["created_at"], t["created_at"]))
        for c in t.get("comments", []):
            db.execute("insert into comments(ticket_id,author,body,created_at) values(?,?,?,?)",
                       (t["id"], c["author"], c["body"], c["created_at"]))
    for j in data["jobs"]:
        db.execute("insert into jobs values(?,?,?,?,?,?)",
                   (j["id"], j["account_id"], j["slide_count"], j["priority"],
                    j["status"], j["submitted_at"]))
    for c in data["config"]:
        db.execute("insert into config values(?,?,?,?,?)",
                   (c["key"], json.dumps(c["value"]), 1, c["description"], now()))
    db.commit()

# ------------------------------------------------------------------ http layer
class ApiError(Exception):
    def __init__(self, status, code, message, retryable=False, hint=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable, self.hint = retryable, hint

    def payload(self):
        e = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            e["hint"] = self.hint
        return {"error": e}

def ticket_row(db, tid, who=None):
    r = db.execute("select * from tickets where id=?", (tid,)).fetchone()
    # A ticket this caller may not see is reported exactly like one that does
    # not exist: a 403 would confirm to an attacker that T-1007 is real.
    if not r or (who and not visible(who, r["account_id"])):
        raise ApiError(404, "not_found", f"no ticket {tid}",
                       hint="Use search_tickets to find valid ticket ids (format T-1001).")
    t = dict(r)
    t["comments"] = [dict(c) for c in db.execute(
        "select author, body, created_at from comments where ticket_id=? order by id", (tid,))]
    return t

def visible(who, account_id):
    return who["accounts"] == "*" or account_id in who["accounts"]

class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # the client hung up; there is no one left to answer

    protocol_version = "HTTP/1.1"
    server_version = "aira-ops/1.0"

    def log_message(self, fmt, *args):
        if not self.server.quiet:
            super().log_message(fmt, *args)

    # -- plumbing
    def _send(self, status, obj):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        raw = self._raw_body
        if not raw:
            return {}
        try:
            v = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "invalid", "request body is not valid JSON")
        if not isinstance(v, dict):
            raise ApiError(400, "invalid", "request body must be a JSON object")
        return v

    def _auth(self):
        """Who is calling - decided by the token, never by a header."""
        got = self.headers.get("Authorization", "")
        tok = got[7:] if got.startswith("Bearer ") else ""
        callers = getattr(self.server, "callers", {}) or {}
        if tok and sha256(tok) in callers:
            self.who = dict(callers[sha256(tok)])
            return
        want = self.server.token
        if want and tok == want:
            # The shared token: every holder is the same caller. X-Actor is a
            # label the caller chose, and the audit log records it as unverified.
            self.who = {"actor": self.headers.get("X-Actor", "unknown"), "accounts": "*",
                        "write": True, "verified": False}
            return
        raise ApiError(401, "unauthorised", "missing or wrong bearer token",
                       hint="Set AIRA_OPS_TOKEN in the environment of the caller.")

    def _actor(self):
        return self.who["actor"]

    def _require_write(self):
        if not self.who["write"]:
            raise ApiError(403, "forbidden", f"{self.who['actor']} is not allowed to write",
                           hint="This caller is read-only. A human with write access has to make this change.")

    def _dispatch(self, method):
        # Read the whole body before anything else, even if we are about to
        # reject the request. On a keep-alive connection an unread body is
        # parsed as the NEXT request, and the client gets a baffling 501.
        n = int(self.headers.get("Content-Length") or 0)
        self._raw_body = self.rfile.read(n) if n else b""
        db = self.server.db
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        try:
            if parts == ["health"]:
                return self._send(200, {"ok": True, "service": "aira-ops"})
            self._auth()
            if self.server.latency:
                time.sleep(self.server.latency)
            with DB_LOCK:
                status, obj = self._route(db, method, parts, q)
            self._send(status, obj)
        except ApiError as e:
            self._send(e.status, e.payload())
        except Exception as e:  # never leak a stack trace to a caller
            self._send(500, {"error": {"code": "internal", "message": type(e).__name__,
                                       "retryable": True}})

    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_PATCH(self): self._dispatch("PATCH")
    def do_PUT(self): self._dispatch("PUT")
    def do_DELETE(self): self._dispatch("DELETE")

    # -- idempotent writes
    def _idempotent(self, db, method, path, fn):
        self._require_write()
        key = self.headers.get("Idempotency-Key")
        if not key:
            raise ApiError(400, "invalid", "writes require an Idempotency-Key header",
                           hint="Send a fresh UUID per logical write; reuse it on retry.")
        # A key names ONE operation: this caller, this route, this exact payload.
        # Scoped per caller so two callers can't collide (or read each other's
        # stored results); bound to the payload so a reused key can't smuggle in
        # a different write and get the old answer back.
        scope = self.who["actor"] if self.who["verified"] else "shared"
        fp = sha256(f"{method} {path}\n" + self._raw_body.decode("utf-8", "replace"))
        prior = db.execute("select * from idem where scope=? and key=?", (scope, key)).fetchone()
        if prior:
            if prior["fingerprint"] != fp:
                raise ApiError(422, "invalid", "Idempotency-Key was already used for a different request",
                               hint="Use a new key for a new operation; reuse a key only to retry the identical request.")
            body = json.loads(prior["body"])
            body["_replayed"] = True
            return prior["status"], body
        status, obj = fn()
        db.execute("insert into idem values(?,?,?,?,?,?,?,?)",
                   (scope, key, fp, method, path, status, json.dumps(obj), now()))
        db.commit()
        return status, obj

    def _audit(self, db, action, target, detail):
        claimed = self.headers.get("X-Actor")
        if self.who["verified"] and claimed and claimed != self.who["actor"]:
            detail = dict(detail, claimed_actor=claimed)   # evidence of an impersonation attempt
        db.execute("insert into audit(at,actor,action,target,detail,verified) values(?,?,?,?,?,?)",
                   (now(), self._actor(), action, target, json.dumps(detail), int(self.who["verified"])))

    # -- routes
    def _route(self, db, method, parts, q):
        path = "/" + "/".join(parts)

        # tickets -----------------------------------------------------------
        if parts == ["tickets"] and method == "GET":
            sql, args = "select id,title,status,priority,account_id,assignee,updated_at from tickets where 1=1", []
            if q.get("status"):
                if q["status"] not in TICKET_STATUSES:
                    raise ApiError(400, "invalid", f"status must be one of {', '.join(TICKET_STATUSES)}")
                sql += " and status=?"; args.append(q["status"])
            if q.get("account_id"):
                sql += " and account_id=?"; args.append(q["account_id"])
            if q.get("priority"):
                sql += " and priority=?"; args.append(q["priority"])
            if q.get("q"):
                sql += " and (title like ? or body like ?)"; args += [f"%{q['q']}%"] * 2
            if self.who["accounts"] != "*":
                sql += f" and account_id in ({','.join('?' * len(self.who['accounts']))})"
                args += list(self.who["accounts"])
            limit = min(int(q.get("limit", 20)), 50)
            rows = [dict(r) for r in db.execute(sql + " order by updated_at desc limit ?", args + [limit])]
            return 200, {"count": len(rows), "tickets": rows}

        if len(parts) == 2 and parts[0] == "tickets" and method == "GET":
            return 200, ticket_row(db, parts[1], self.who)

        if parts == ["tickets"] and method == "POST":
            def create():
                b = self._json_body()
                errs = []
                if not str(b.get("title", "")).strip():
                    errs.append("title is required")
                if b.get("priority", "P3") not in PRIORITIES:
                    errs.append("priority must be one of P1, P2, P3, P4")
                acct = b.get("account_id")
                if not acct or not visible(self.who, acct) or not db.execute("select 1 from accounts where id=?", (acct,)).fetchone():
                    errs.append(f"account_id {acct!r} does not exist")
                if errs:
                    raise ApiError(400, "invalid", "; ".join(errs),
                                   hint="Use lookup_account to confirm an account id first.")
                n = db.execute("select count(*) from tickets").fetchone()[0]
                tid = f"T-{1001 + n}"
                db.execute("insert into tickets values(?,?,?,?,?,?,?,?,?)",
                           (tid, b["title"].strip(), b.get("body", ""), "open",
                            b.get("priority", "P3"), acct, None, now(), now()))
                self._audit(db, "ticket.create", tid, {"title": b["title"]})
                db.commit()
                return 201, ticket_row(db, tid)
            return self._idempotent(db, method, path, create)

        if len(parts) == 3 and parts[0] == "tickets" and parts[2] == "comments" and method == "POST":
            tid = parts[1]
            def comment():
                ticket_row(db, tid, self.who)
                b = self._json_body()
                if not str(b.get("body", "")).strip():
                    raise ApiError(400, "invalid", "comment body is required")
                db.execute("insert into comments(ticket_id,author,body,created_at) values(?,?,?,?)",
                           (tid, self._actor(), b["body"].strip(), now()))
                db.execute("update tickets set updated_at=? where id=?", (now(), tid))
                self._audit(db, "ticket.comment", tid, {"len": len(b["body"])})
                db.commit()
                return 201, ticket_row(db, tid)
            return self._idempotent(db, method, path, comment)

        if len(parts) == 2 and parts[0] == "tickets" and method == "PATCH":
            tid = parts[1]
            def patch():
                t = ticket_row(db, tid, self.who)
                b = self._json_body()
                new = b.get("status")
                if new not in TICKET_STATUSES:
                    raise ApiError(400, "invalid", f"status must be one of {', '.join(TICKET_STATUSES)}")
                if new not in TRANSITIONS[t["status"]]:
                    allowed = sorted(TRANSITIONS[t["status"]]) or ["none - closed is final"]
                    raise ApiError(409, "conflict", f"cannot move {tid} from {t['status']} to {new}",
                                   hint="Allowed from here: " + ", ".join(allowed))
                db.execute("update tickets set status=?, updated_at=? where id=?", (new, now(), tid))
                self._audit(db, "ticket.status", tid, {"from": t["status"], "to": new})
                db.commit()
                return 200, ticket_row(db, tid)
            return self._idempotent(db, method, path, patch)

        # accounts ------------------------------------------------------------
        if len(parts) == 2 and parts[0] == "accounts" and method == "GET":
            aid = parts[1]
            if not re.fullmatch(r"ACC-\d{4}", aid):
                raise ApiError(400, "invalid", f"{aid!r} is not an account id",
                               hint="Account ids look like ACC-1001.")
            r = db.execute("select * from accounts where id=?", (aid,)).fetchone()
            if not r or not visible(self.who, aid):
                raise ApiError(404, "not_found", f"no account {aid}")
            a = dict(r)
            a["open_tickets"] = db.execute(
                "select count(*) from tickets where account_id=? and status in ('open','in_progress')",
                (aid,)).fetchone()[0]
            return 200, a

        # jobs ----------------------------------------------------------------
        if parts == ["jobs"] and method == "GET":
            sql, args = "select * from jobs where 1=1", []
            for f in ("status", "account_id"):
                if q.get(f):
                    sql += f" and {f}=?"; args.append(q[f])
            if self.who["accounts"] != "*":
                sql += f" and account_id in ({','.join('?' * len(self.who['accounts']))})"
                args += list(self.who["accounts"])
            rows = [dict(r) for r in db.execute(sql + " order by submitted_at desc limit 50", args)]
            return 200, {"count": len(rows), "jobs": rows}
        if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
            r = db.execute("select * from jobs where id=?", (parts[1],)).fetchone()
            if not r or not visible(self.who, r["account_id"]):
                raise ApiError(404, "not_found", f"no job {parts[1]}")
            return 200, dict(r)

        # config --------------------------------------------------------------
        if parts == ["config"] and method == "GET":
            rows = [{"key": r["key"], "value": json.loads(r["value"]), "version": r["version"],
                     "description": r["description"]} for r in db.execute("select * from config order by key")]
            return 200, {"config": rows}
        if len(parts) == 2 and parts[0] == "config" and method == "GET":
            r = db.execute("select * from config where key=?", (parts[1],)).fetchone()
            if not r:
                raise ApiError(404, "not_found", f"no config key {parts[1]}",
                               hint="GET /config lists every key.")
            return 200, {"key": r["key"], "value": json.loads(r["value"]),
                         "version": r["version"], "description": r["description"]}
        if len(parts) == 2 and parts[0] == "config" and method == "PUT":
            key = parts[1]
            def put():
                r = db.execute("select * from config where key=?", (key,)).fetchone()
                if not r:
                    raise ApiError(404, "not_found", f"no config key {key}")
                b = self._json_body()
                if "value" not in b or "expected_version" not in b:
                    raise ApiError(400, "invalid", "body needs value and expected_version")
                # Validation at the boundary: a write may change a value, never its
                # type. Found live: a model sent "16" (a string) for an integer key,
                # and a loose schema upstream let it through.
                cur = json.loads(r["value"])
                def kind(v):
                    return "boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else type(v).__name__
                if kind(b["value"]) != kind(cur):
                    raise ApiError(400, "invalid",
                                   f"{key} is a {kind(cur)}; got a {kind(b['value'])} ({b['value']!r})",
                                   hint=f"Send a {kind(cur)}, e.g. {json.dumps(cur)}.")
                if b["expected_version"] != r["version"]:
                    raise ApiError(409, "conflict",
                                   f"{key} is at version {r['version']}, not {b['expected_version']}",
                                   hint="Re-read the key, then retry with the current version.")
                db.execute("update config set value=?, version=version+1, updated_at=? where key=?",
                           (json.dumps(b["value"]), now(), key))
                self._audit(db, "config.update", key, {"from": json.loads(r["value"]), "to": b["value"]})
                db.commit()
                r2 = db.execute("select * from config where key=?", (key,)).fetchone()
                return 200, {"key": key, "value": json.loads(r2["value"]), "version": r2["version"]}
            return self._idempotent(db, method, path, put)

        # audit ---------------------------------------------------------------
        if parts == ["audit"] and method == "GET":
            if self.who["accounts"] != "*":
                raise ApiError(403, "forbidden", "the audit log is not visible to account-scoped callers")
            rows = [dict(r) for r in db.execute("select * from audit order by id desc limit 50")]
            return 200, {"count": len(rows), "entries": rows}

        raise ApiError(404, "not_found", f"no route {method} {path}")

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("AIRA_OPS_PORT", 8150)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=str(HERE / "aira_ops.sqlite"))
    ap.add_argument("--reset", action="store_true", help="delete the database and reseed")
    ap.add_argument("--latency", type=float, default=0.0, help="seconds to add to every call")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--callers", default=os.environ.get("AIRA_OPS_CALLERS"),
                    help="JSON file of per-caller token hashes and permissions")
    ap.add_argument("--issue-token", metavar="ACTOR", help="add a caller to --callers, print its token once, exit")
    ap.add_argument("--accounts", default="*", help="with --issue-token: comma-separated account ids, or *")
    ap.add_argument("--write", action="store_true", help="with --issue-token: allow writes")
    a = ap.parse_args()
    if a.issue_token:
        if not a.callers:
            raise SystemExit("--issue-token needs --callers FILE")
        import secrets
        tok = secrets.token_hex(16)
        f = Path(a.callers)
        data = json.loads(f.read_text()) if f.exists() else {"callers": []}
        data["callers"] = [c for c in data["callers"] if c["actor"] != a.issue_token]
        data["callers"].append({"actor": a.issue_token, "token_sha256": sha256(tok),
                                "accounts": "*" if a.accounts == "*" else a.accounts.split(","),
                                "write": a.write})
        f.write_text(json.dumps(data, indent=2) + "\n")
        print(tok)   # shown once; only its hash is stored
        return
    token = os.environ.get("AIRA_OPS_TOKEN")
    if not token:
        raise SystemExit("AIRA_OPS_TOKEN is not set. It is a secret: put it in your shell "
                         "or .env, never in code. Example:\n"
                         "  export AIRA_OPS_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')")
    if a.reset and Path(a.db).exists():
        Path(a.db).unlink()
    fresh = not Path(a.db).exists()
    db = connect(a.db)
    if fresh:
        seed(db)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.db, srv.token, srv.latency, srv.quiet = db, token, a.latency, a.quiet
    srv.callers = load_callers(a.callers)
    print(f"aira-ops on http://{a.host}:{a.port}  (db: {Path(a.db).name}{', fresh seed' if fresh else ''}"
          f"{f', {len(srv.callers)} verified callers' if srv.callers else ''})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
