"""
The shared state store: the ONLY thing the stages have in common.

Stages never call each other. Each reads what it needs from here and writes its
result back. That is what makes the pipeline checkpointed (a crash loses at
most the stage in flight), resumable (re-running skips finished stages),
inspectable (every hand-off is a row you can read), and gateable (a human
decision is a row too, with a name and a reason).

SQLite, standard library. The same shape works on Postgres or DynamoDB.
"""
import hashlib, json, sqlite3, threading, uuid
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
def now():
    return datetime.now(IST).isoformat(timespec="seconds")

SCHEMA = """
create table if not exists runs(id text primary key, account_id text, question text,
    status text, created_at text, updated_at text);
create table if not exists stages(run_id text, name text, status text, attempt integer,
    output text, error text, cost_usd real, tool_calls integer, started_at text, finished_at text,
    primary key(run_id, name));
create table if not exists approvals(run_id text primary key, decision text, approver text,
    reason text, override integer, at text, proposal_sha text);
create table if not exists operations(run_id text primary key, op_id text, action text,
    payload text, status text, response text, created_at text, updated_at text);
"""

# Run statuses, in order. The pipeline moves forward only.
#   created -> investigated -> reviewed -> awaiting_approval -> approved -> applied
#                                      \-> needs_rework        \-> rejected
#   apply can end in: applied | apply_failed | outcome_unknown (resume retries safely)
TERMINAL = ("applied", "rejected", "no_change")

class Conflict(RuntimeError):
    """Two writers raced for a row only one of them may create (e.g. two people deciding one run)."""

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("pragma table_info(approvals)")}
        if "proposal_sha" not in cols:            # a runs.sqlite from before this column existed
            self.db.execute("alter table approvals add column proposal_sha text")
        self.lock = threading.Lock()

    # ---- runs
    def create_run(self, account_id, question):
        rid = uuid.uuid4().hex[:10]
        with self.lock:
            self.db.execute("insert into runs values(?,?,?,?,?,?)", (rid, account_id, question, "created", now(), now()))
            self.db.commit()
        return rid

    def run(self, rid):
        r = self.db.execute("select * from runs where id=?", (rid,)).fetchone()
        if not r:
            raise KeyError(f"no run {rid}")
        return dict(r)

    def runs(self, limit=20):
        return [dict(r) for r in self.db.execute("select * from runs order by created_at desc limit ?", (limit,))]

    def set_status(self, rid, status):
        with self.lock:
            self.db.execute("update runs set status=?, updated_at=? where id=?", (status, now(), rid))
            self.db.commit()

    # ---- stages: the checkpoint
    def stage(self, rid, name):
        r = self.db.execute("select * from stages where run_id=? and name=?", (rid, name)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["output"] = json.loads(d["output"]) if d["output"] else None
        return d

    def stage_started(self, rid, name):
        prev = self.stage(rid, name)
        attempt = (prev["attempt"] + 1) if prev else 1
        spent = prev["cost_usd"] if prev else 0.0          # failed attempts were paid for too
        with self.lock:
            self.db.execute("insert or replace into stages values(?,?,?,?,?,?,?,?,?,?)",
                            (rid, name, "running", attempt, None, None, spent, 0, now(), None))
            self.db.commit()
        return attempt

    def stage_done(self, rid, name, output, cost_usd, tool_calls):
        with self.lock:
            self.db.execute("update stages set status='done', output=?, cost_usd=cost_usd+?, tool_calls=?, finished_at=?, error=null "
                            "where run_id=? and name=?", (json.dumps(output), cost_usd, tool_calls, now(), rid, name))
            self.db.commit()

    def stage_failed(self, rid, name, error, cost_usd=0.0):
        with self.lock:
            self.db.execute("update stages set status='failed', error=?, cost_usd=cost_usd+?, finished_at=? where run_id=? and name=?",
                            (str(error)[:500], cost_usd, now(), rid, name))
            self.db.commit()

    # ---- the human gate
    def approval(self, rid):
        r = self.db.execute("select * from approvals where run_id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def proposal_sha(self, rid):
        """Fingerprint of the exact change a human is deciding on."""
        st = self.stage(rid, "investigate")
        change = (st or {}).get("output", {}) and st["output"].get("proposed_change")
        return hashlib.sha256(json.dumps(change, sort_keys=True).encode()).hexdigest()

    def record_decision(self, rid, decision, approver, reason, override=False):
        # The decision is bound to the proposal as it was when the human saw it.
        with self.lock:
            try:
                self.db.execute("insert into approvals(run_id, decision, approver, reason, override, at, proposal_sha) "
                                "values(?,?,?,?,?,?,?)",
                                (rid, decision, approver, reason, int(override), now(), self.proposal_sha(rid)))
            except sqlite3.IntegrityError:        # the primary key is the real "one decision" guarantee
                raise Conflict(f"run {rid} was already decided") from None
            self.db.commit()

    # ---- the write: its operation id is stored BEFORE it is sent
    def operation(self, rid):
        r = self.db.execute("select * from operations where run_id=?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def record_operation(self, rid, op_id, action, payload):
        with self.lock:
            self.db.execute("insert into operations values(?,?,?,?,?,?,?,?)",
                            (rid, op_id, action, json.dumps(payload), "pending", None, now(), now()))
            self.db.commit()

    def operation_result(self, rid, status, response):
        with self.lock:
            self.db.execute("update operations set status=?, response=?, updated_at=? where run_id=?",
                            (status, json.dumps(response)[:4000], now(), rid))
            self.db.commit()

    def cost(self, rid):
        return round(self.db.execute("select coalesce(sum(cost_usd),0) from stages where run_id=?", (rid,)).fetchone()[0], 4)
