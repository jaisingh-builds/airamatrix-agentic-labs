"""Local mode - your own aira-ops, the training gateway, no AWS. Same commands, flags, output and exit codes
as the Java (capstone-cli.jar) and Node (capstone.mjs) solutions.

  python3 capstone.py tokens --account ACC-1001          read token (agent) + apply token (human), shown once
  python3 capstone.py run --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
  python3 capstone.py show RUN | list | trace RUN
  python3 capstone.py approve RUN --by "Name" --reason "why"      (or reject)
  python3 capstone.py apply RUN                                   (its own shell: AIRA_OPS_APPLY_TOKEN)
  python3 capstone.py replay ../fixtures/blocked-leak.json        (a saved proposal through the guardrail, $0)
  python3 capstone.py eval [--repeat 2] [--cases a,b] [--budget 1.5] | eval --regrade results/x.json

Environment: AIRA_OPS_URL (default http://127.0.0.1:8150), AIRA_OPS_READ_TOKEN (run/replay), AIRA_OPS_APPLY_TOKEN
(apply only), CAPSTONE_DB (default python/out/capstone-runs.sqlite), LAB_TRACE_DIR (default <repo>/traces), .env for the gateway.

Exit codes: 0 ok, 1 failed, 2 setup error / could not run, 3 refused (guardrail block, gate refusal, bad arguments).
"""
import getpass, json, os, sys
from datetime import datetime
from pathlib import Path

from . import evals, gate, guardrails, repo, responder, sla as sla_mod
from .agent import FORBIDDEN_ENV, ResponderAgent
from .gate import GateError, HttpOpsWriter
from .ops import HttpOpsReader, PrivateOps, hex_, issue
from .repo import spans
from .store import Store, new_id
from .util import ArgError, SetupError, dumps, iso, jfmt, parse_instant

USAGE = """\
python3 capstone.py <command>      (local mode: your aira-ops + the training gateway)
  tokens  --account ACC-1001 [--callers FILE]
  run     --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question TEXT] [--budget USD] [--max-turns N]
  show RUN | list | trace RUN
  approve RUN --by NAME --reason WHY        reject RUN --by NAME --reason WHY
  apply   RUN                                (needs AIRA_OPS_APPLY_TOKEN; starts no agent)
  replay  FIXTURE.json                       (a saved proposal through the guardrail; no model)
  eval    [--repeat N] [--cases a,b] [--budget USD] [--golden F] | eval --regrade RESULTS.json"""


class Opts:
    """--key value options and positional arguments."""

    def __init__(self, args):
        self.kv, self.positional = {}, []
        i = 0
        while i < len(args):
            if args[i].startswith("--"):
                if i + 1 >= len(args):
                    raise ArgError(f"{args[i]} needs a value")
                self.kv[args[i][2:]] = args[i + 1]
                i += 2
            else:
                self.positional.append(args[i])
                i += 1

    def has(self, k):
        return k in self.kv

    def get(self, k, default=None):
        return self.kv.get(k, default)

    def need(self, k):
        if k not in self.kv:
            raise ArgError(f"--{k} is required")
        return self.kv[k]

    def int_or(self, k, d):
        try:
            return int(self.kv[k]) if k in self.kv else d
        except ValueError:
            raise ArgError(f'For input string: "{self.kv[k]}"') from None

    def float_or(self, k, d):
        try:
            return float(self.kv[k]) if k in self.kv else d
        except ValueError:
            raise ArgError(f'For input string: "{self.kv[k]}"') from None

    def pos(self, i):
        if len(self.positional) <= i:
            raise ArgError("missing RUN id / file")
        return self.positional[i]


def need(env, k, what):
    v = env.get(k, "")
    if not v.strip():
        raise SetupError(f"{k} is not set - {what}")
    return v


def main(argv=None, env=None, out=None, err=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    out, err = out or sys.stdout, err or sys.stderr
    if not argv or argv[0].startswith("-h"):
        print(USAGE, file=out)
        return 2 if not argv else 0
    cmd = argv[0]
    url = env.get("AIRA_OPS_URL") or "http://127.0.0.1:8150"
    try:
        o = Opts(argv[1:])
        if cmd == "tokens":
            return tokens(o, out)
        if cmd == "eval":
            return eval_(o, env, out)
        with Store(env.get("CAPSTONE_DB") or default_db()) as store:
            if cmd == "run":
                read = need(env, "AIRA_OPS_READ_TOKEN", "the agent's read-only token (capstone tokens)")
                acc = o.need("account")
                as_of = parse_instant(o.get("as-of")) if o.has("as-of") else datetime.now().astimezone()
                rid = store.create_run(new_id(), acc, iso(as_of), o.get("question"), "local")
                tr = spans.Tracer("capstone", trace_id=rid)
                print(f"run {rid} started · trace {tr.path}", file=out)
                from agentic_core import Config, GatewayClient
                cfg = Config()
                try:
                    gw = GatewayClient(cfg)
                except SystemExit as e:                  # labkit's "copy .env.example to .env" message
                    raise SetupError(str(e)) from None
                agent = ResponderAgent(gw.messages, cfg.model, o.int_or("max-turns", max(cfg.max_steps, 10)),
                                       o.float_or("budget", cfg.budget_usd), env)
                res = responder.run(rid, acc, as_of, o.get("question"), HttpOpsReader(url, read), agent, tr)
                responder.save(store, res, str(tr.path))
                show(store, rid, out)
                return {"blocked": 3, "failed": 1, "guardrail_intervened": 1}.get(res.status, 0)
            if cmd == "replay":
                read = need(env, "AIRA_OPS_READ_TOKEN", "a read-only token: the guardrail verifies against live data")
                path = o.pos(0)
                fx = json.loads(Path(path).read_text(encoding="utf-8"))
                rid = replay(store, fx, HttpOpsReader(url, read))
                print(f"run {rid} replayed from {path} (no model, $0)", file=out)
                show(store, rid, out)
                return 3 if store.run(rid).status == "blocked" else 0
            if cmd == "show":
                show(store, o.pos(0), out)
                return 0
            if cmd == "list":
                for r in store.runs(20):
                    print(f"{r.id}  {r.mode:<6} {r.account_id:<9} {r.status:<19} ${jfmt(r.cost_usd, 4):<7} {r.as_of}", file=out)
                return 0
            if cmd == "trace":
                from trace_view import load, render      # common/trace_view.py - reads every language's traces
                render(load(store.run(o.pos(0)).trace), out)
                return 0
            if cmd in ("approve", "reject"):
                r = store.run(o.pos(0))
                gate.decide(store, r.id, cmd, o.get("by"), "os:" + getpass.getuser(), o.get("reason"), trace_for(r))
                show(store, r.id, out)
                return 0
            if cmd == "apply":
                write = need(env, "AIRA_OPS_APPLY_TOKEN", "the apply step's own write token (capstone tokens)")
                r = store.run(o.pos(0))
                gate.apply(store, r.id, HttpOpsWriter(url, write), url, trace_for(r))
                show(store, r.id, out)
                return 0 if store.run(r.id).status == "applied" else 1
            print(f"unknown command {cmd}\n{USAGE}", file=err)
            return 2
    except (GateError, ArgError) as e:
        print(f"refused: {e}", file=err)
        return 3
    except SetupError as e:
        print(str(e), file=err)
        return 2
    except Exception as e:
        print(f"{type(e).__name__}: {spans.redact(str(e), limit=None)}", file=err)
        return 1


def default_db():
    """python/out/ (gitignored): the store, the callers file and your aira-ops database never land in a tracked folder."""
    out = repo.python_dir() / "out"
    out.mkdir(parents=True, exist_ok=True)
    return str(out / "capstone-runs.sqlite")


def trace_for(r):
    return spans.Tracer("capstone", trace_id=r.id)   # same file as the run, as long as LAB_TRACE_DIR is the same


def replay(store, fx, ops):
    """A saved proposal through the SAME guardrail and gate - every learner gets the refusal on demand."""
    acc = fx.get("account", "")
    as_of = parse_instant(fx.get("as_of"))
    rid = store.create_run(new_id(), acc, iso(as_of), f"{fx.get('question', '')} [replay of {fx.get('source', '')}]", "local")
    tr = spans.Tracer("capstone", trace_id=rid)
    with tr.span("replay", account=acc, stage="sla-responder") as root:
        with tr.span("guardrail.verify", stage="code-guardrail") as vs:
            sla = sla_mod.compute(ops, acc, as_of)
            v = guardrails.verify(fx.get("proposal", {}), sla)
            vs.set(verdict="pass" if v.passed else "block", denials=v.rules())
            if not v.passed:
                vs.fail("guardrail refused: " + ", ".join(v.rules()))
        root.set(verdict="pass" if v.passed else "blocked")
    action = ((fx.get("proposal") or {}).get("action") or {}).get("type", "")
    status = "blocked" if not v.passed else "no_action" if action == "none" else "awaiting_approval"
    store.finish_run(rid, status, 0, 0, 0, None, str(tr.path))
    store.save_proposal(rid, fx.get("proposal", {}), sla.to_json(), v.to_json(), [])
    return rid


def show(store, rid, out):
    r = store.run(rid)
    print(f"run {r.id} · {r.mode} · {r.account_id} · as_of {r.as_of} · {r.status} · ${jfmt(r.cost_usd, 4)} · "
          f"{r.turns} turns · {r.tool_calls} tool calls", file=out)
    if r.question is not None:
        print(f"  request: {r.question}", file=out)
    if r.error is not None:
        print(f"  error:   {r.error}", file=out)
    p = store.proposal(rid)
    if p is not None:
        if p.sla is not None:
            print("\n[sla] computed by code at as_of:", file=out)
            for i in p.sla.get("items", []):
                print(f"  {i.get('item', ''):<7} {i.get('kind', ''):<7} {i.get('status', ''):<12} "
                      f"{int(i.get('elapsed_minutes', 0)):>5} / {int(i.get('target_minutes', 0)):<5} min  "
                      f"{int(i.get('pct_of_target', 0)):>3}%  {i.get('state', '')}", file=out)
        pr = p.proposal or {}
        print("\n[proposal]", file=out)
        print(f"  summary: {pr.get('summary', '')}", file=out)
        print(f"  cause:   {pr.get('likely_cause', '')}", file=out)
        ex = [f"{e.get('item', '')} {e.get('state', '')}" for e in pr.get("exposed", [])]
        print(f"  exposed: {', '.join(ex) if ex else '(none)'}", file=out)
        if pr.get("untrusted_instructions_seen"):
            print(f"  flagged: {dumps(pr['untrusted_instructions_seen'])} (instructions in ticket text - not followed)", file=out)
        a = pr.get("action", {})
        print(f"  action:  {a.get('type', '')}{' on ' + a['ticket_id'] if 'ticket_id' in a else ''} - {a.get('reason', '')}", file=out)
        if "comment" in a:
            print("  comment (customer-visible):\n    " + str(a["comment"]).replace("\n", "\n    "), file=out)
        if p.verdict is not None:
            v = guardrails.Verdict.from_json(p.verdict)
            print(f"\n[guardrail] {'PASS - every rule' if v.passed else 'BLOCKED'}", file=out)
            for d in v.denials:
                print(f"  x {d['rule']}: {d['detail']}", file=out)
    ap = store.approval(rid)
    if ap is not None:
        print(f"\n[gate] {ap.decision} by {ap.approver} ({ap.principal}) at {ap.at}: {ap.reason}", file=out)
    op = store.operation(rid)
    if op is not None:
        print(f"\n[apply] {op.status} · op {op.op_id} · {op.response}", file=out)
    if r.status == "awaiting_approval":
        print(f"\nnext: approve {rid} --by \"Your Name\" --reason \"why\"   (or reject)", file=out)
    if r.status == "approved" and r.mode == "local":
        print(f"\nnext: apply {rid}   (in the shell that holds AIRA_OPS_APPLY_TOKEN)", file=out)


def tokens(o, out):
    acc = o.need("account")
    callers = Path(o.get("callers", str(repo.python_dir() / "out" / "capstone-callers.json"))).resolve()
    callers.parent.mkdir(parents=True, exist_ok=True)
    admin = "unused-" + hex_(4)            # --issue-token only edits the callers file
    read = issue(callers, admin, "sla-responder", acc, False)
    write = issue(callers, admin, "capstone-apply", acc, True)
    print(f"# Shown once; {callers.name} keeps only their SHA-256. Both are scoped to {acc}.", file=out)
    print(f"export AIRA_OPS_READ_TOKEN={read}     # shell 1: the agent (read-only)", file=out)
    print(f"export AIRA_OPS_APPLY_TOKEN={write}    # shell 2: apply, the human's step - never in shell 1", file=out)
    print(f'# PowerShell: $env:AIRA_OPS_READ_TOKEN="{read}"  /  $env:AIRA_OPS_APPLY_TOKEN="{write}"', file=out)
    print("# start YOUR aira-ops with this callers file and your own db and port, e.g.:", file=out)
    print(f"#   python3 \"{repo.ops_script()}\" --port 8177 --db \"{callers.parent / 'capstone-ops.sqlite'}\" "
          f"--callers \"{callers}\" --reset", file=out)
    return 0


def eval_(o, env, out):
    golden = evals.load(o.get("golden", str(repo.solution() / "golden" / "cases.json")))
    if o.has("regrade"):
        return evals.regrade(golden, o.get("regrade"), out)
    cases = evals.select(golden, o.get("cases"))
    if not cases:
        print("no matching cases", file=out)
        return 2
    from agentic_core import Config, GatewayClient
    try:
        cfg = Config().require()
    except SystemExit as e:
        print(f"setup: {e}", file=out)
        return 2
    for k in FORBIDDEN_ENV:
        if env.get(k):
            print(f"setup: unset {k} - evals start agents", file=out)
            return 2
    gw = GatewayClient(cfg)
    max_turns = o.int_or("max-turns", 10)

    def make_agent(budget):
        return ResponderAgent(gw.messages, cfg.model, max_turns, budget, env)

    with PrivateOps(evals.accounts(cases)) as ops:
        return evals.execute(golden, cases, o.int_or("repeat", 1), o.int_or("workers", 3), o.float_or("budget", 1.5),
                             "local", evals.local_runner(ops, make_agent, o.float_or("per-run-budget", 0.30)),
                             o.get("out", str(repo.python_dir() / "results")), out)
