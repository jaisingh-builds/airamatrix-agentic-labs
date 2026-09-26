#!/usr/bin/env python3
"""
Lab 5.1 - a two-stage agent pipeline with a shared state store and a human gate.

    python3 pipeline.py run --account ACC-1001 --question "Ingest backlog on T-1001"
    python3 pipeline.py show <run>            # every hand-off, as stored
    python3 pipeline.py approve <run> --by "Jai" --reason "memory fix confirmed in INC-88"
    python3 pipeline.py reject  <run> --by "Jai" --reason "..."
    python3 pipeline.py apply   <run>          # only after approve; safe to re-run
    python3 pipeline.py resume  <run>          # finish whatever is unfinished
    python3 pipeline.py list

    investigate (agent) -> review (agent) -> HUMAN GATE -> apply (plain code)
            \\______________ shared state store: runs.sqlite ______________/

Credentials, least privilege:
    AIRA_OPS_READ_TOKEN   the agents' token: read-only, scoped to the account
    AIRA_OPS_APPLY_TOKEN  the apply step's token: may write. No agent ever sees it.
    (python3 pipeline.py tokens --account ACC-1001 issues both into aira-ops' callers.json)
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
from contracts import PROPOSAL, VERDICT, ALLOWED_ACTIONS, validate, check_change, ContractError  # noqa: E402
from store import Store, TERMINAL, Conflict  # noqa: E402
from spans import Tracer  # noqa: E402
import agents  # noqa: E402

OPS_URL = os.environ.get("AIRA_OPS_URL", "http://127.0.0.1:8150").rstrip("/")
APPLY_TIMEOUT_S = float(os.environ.get("APPLY_TIMEOUT_S", 8))

class GateError(RuntimeError):
    """Raised when something tries to cross the human gate without a human."""

# --------------------------------------------------------------- the stages
def run_stage(store, runner, tracer, rid, name, system, prompt, schema, extra_check=None):
    """One agent stage, checkpointed: a finished stage is never run (or paid for) twice."""
    # >>> TODO 1: checkpoint - a finished stage is never run (or paid for) twice
    done = store.stage(rid, name)
    if done and done["status"] == "done":
        tracer.event(f"stage.{name}.skipped", reason="checkpoint: already done")
        return done["output"]
    # <<< TODO 1
    attempt = store.stage_started(rid, name)
    with tracer.span(f"stage.{name}", attempt=attempt) as sp:
        try:
            res = runner.run(name, system, prompt, schema)
            validate(res.output, schema)                 # the contract, enforced - not hoped for
            if extra_check:
                extra_check(res.output)
        except Exception as e:
            cost = getattr(e, "cost_usd", None) or getattr(locals().get("res"), "cost_usd", 0.0)
            sp.set(cost_usd=round(cost, 4))
            store.stage_failed(rid, name, f"{type(e).__name__}: {e}", cost)
            store.set_status(rid, f"{name}_failed")
            raise
        for i, (tool, args) in enumerate(res.tool_calls):
            tracer.event("tool_call", tool=tool, input=args, ok=(res.tool_ok[i] if i < len(res.tool_ok) else None))
        sp.set(cost_usd=round(res.cost_usd, 4), tool_calls=len(res.tool_calls), turns=res.turns)
        store.stage_done(rid, name, res.output, res.cost_usd, len(res.tool_calls))
        return res.output

def advance(store, runner, rid, tracer=None):
    """Run every unfinished agent stage, then stop at the gate."""
    r = store.run(rid)
    tracer = tracer or Tracer("lab5-1", trace_id=rid)
    with tracer.span("pipeline.advance", account=r["account_id"]):
        proposal = run_stage(store, runner, tracer, rid, "investigate", agents.INVESTIGATE_SYSTEM,
                             agents.investigate_prompt(r["account_id"], r["question"]), PROPOSAL,
                             lambda o: check_change(o["proposed_change"]))
        store.set_status(rid, "investigated")
        if proposal["proposed_change"]["action"] == "none":
            store.set_status(rid, "no_change")
            return store.run(rid)
        verdict = run_stage(store, runner, tracer, rid, "review", agents.REVIEW_SYSTEM,
                            agents.review_prompt(r["account_id"], r["question"], proposal), VERDICT)
        store.set_status(rid, "reviewed")
        # Only an APPROVE verdict waits for a plain approval. BLOCK and REVISE both mean "not this change":
        # approving the original proposal anyway needs an explicit override and a reason.
        store.set_status(rid, "awaiting_approval" if verdict["verdict"] == "approve" else "needs_rework")
        tracer.event("gate.waiting", verdict=verdict["verdict"])
    return store.run(rid)

# --------------------------------------------------------------- the gate
def decide(store, rid, decision, approver, reason, override=False):
    """A human decision, recorded with a name and a reason. Nothing else opens the gate."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    # >>> TODO 2: the gate - who may decide, when, and what gets recorded
    if not (approver or "").strip() or not (reason or "").strip():
        raise GateError("a decision needs an approver name and a reason")
    r = store.run(rid)
    if store.approval(rid):
        raise GateError(f"run {rid} was already decided")
    if r["status"] == "needs_rework" and decision == "approve" and not override:
        v = ((store.stage(rid, "review") or {}).get("output") or {}).get("verdict")
        what = "blocked this proposal" if v == "block" else "asked for a safer change than this proposal"
        raise GateError(f"the reviewer {what}; approving it needs --override and a reason")
    if r["status"] not in ("awaiting_approval", "needs_rework"):
        raise GateError(f"run {rid} is {r['status']}, not waiting for a decision")
    store.record_decision(rid, decision, approver.strip(), reason.strip(), override)
    store.set_status(rid, "approved" if decision == "approve" else "rejected")
    # <<< TODO 2
    Tracer("lab5-1", trace_id=rid).event("gate.decided", decision=decision, approver=approver, override=override)
    return store.run(rid)

# --------------------------------------------------------------- the only write
def apply(store, rid, write_token, ops_url=OPS_URL, tracer=None):
    """Make the approved change. Plain code, not an agent. Safe to call again."""
    tracer = tracer or Tracer("lab5-1", trace_id=rid)
    r = store.run(rid)
    # >>> TODO 3: no approval on record, no write
    a = store.approval(rid)
    # The gate is checked against the DECISION RECORD, not just the status field.
    if not a or a["decision"] != "approve":
        raise GateError(f"run {rid} has no approval on record")
    # <<< TODO 3
    if a.get("proposal_sha") != store.proposal_sha(rid):
        raise GateError(f"run {rid}: the proposal changed after it was decided - it needs a new decision")
    if r["status"] == "applied":
        return r                     # already done: applying again is a no-op, not an error
    if r["status"] not in ("approved", "outcome_unknown"):
        raise GateError(f"run {rid} is {r['status']}; only an approved run can be applied")
    change = check_change(dict(store.stage(rid, "investigate")["output"]["proposed_change"]))
    if change["action"] not in ALLOWED_ACTIONS or change["action"] == "none":
        raise GateError(f"action {change['action']!r} is not something this pipeline may do")

    # Retry state belongs to the orchestrator: the operation id is stored BEFORE the
    # request is sent, so a crash or timeout followed by `apply` again sends the SAME
    # id and aira-ops applies the change at most once.
    # >>> TODO 4: the operation id is stored BEFORE the request is sent
    op = store.operation(rid)
    if not op:
        store.record_operation(rid, str(uuid.uuid4()), change["action"], change)
        op = store.operation(rid)
    # <<< TODO 4
    if op["status"] == "done":
        return store.run(rid)
    method, path, body = request_for(change)
    with tracer.span("apply", action=change["action"], op_id=op["op_id"], approver=a["approver"]) as sp:
        for attempt in range(3):                       # ride out transient 5xx/timeouts instead of stopping
            key = f"{op['op_id']}-{attempt}"            # unique key per attempt
            status, resp = http(method, ops_url + path, body, write_token, key)
            if status and status < 500:
                break
            time.sleep(0.5 * (attempt + 1))
        sp.set(http_status=status, replayed=bool(isinstance(resp, dict) and resp.get("_replayed")))
        if status in (200, 201):
            store.operation_result(rid, "done", resp); store.set_status(rid, "applied")
        elif status == 0 or status >= 500:
            store.operation_result(rid, "pending", resp); store.set_status(rid, "outcome_unknown")
            sp.fail("outcome unknown - run `apply` again; the same operation id makes it safe")
        else:
            store.operation_result(rid, "failed", resp); store.set_status(rid, "apply_failed")
            sp.fail(f"HTTP {status}")
    return store.run(rid)

def request_for(change):
    if change["action"] == "update_config":
        return "PUT", f"/config/{change['key']}", {"value": change["value"], "expected_version": change["expected_version"]}
    return "POST", f"/tickets/{change['ticket_id']}/comments", {"body": change["comment"]}

def http(method, url, body, token, op_id):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Idempotency-Key": op_id})
    try:
        with urllib.request.urlopen(req, timeout=APPLY_TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": {"code": "unavailable", "message": str(e)}}

def resume(store, runner, rid):
    """Finish the agent stages of a run that stopped. Never writes: an approved run, or one whose
    write has an unknown outcome, is finished with `apply` - a separate process holding the write token."""
    r = store.run(rid)
    if r["status"] in ("created", "investigated", "reviewed") or r["status"].endswith("_failed") and r["status"] != "apply_failed":
        return advance(store, runner, rid)
    return r   # terminal, waiting at the gate, or waiting for `apply`

# --------------------------------------------------------------- CLI
def show(store, rid):
    r = store.run(rid)
    print(f"run {rid} · {r['account_id']} · {r['status']} · ${store.cost(rid)}")
    print(f"  question: {r['question']}")
    for name in ("investigate", "review"):
        s = store.stage(rid, name)
        if s:
            print(f"\n[{name}] {s['status']} · attempt {s['attempt']} · {s['tool_calls']} tool calls · ${round(s['cost_usd'] or 0, 4)}")
            print("  " + json.dumps(s["output"], indent=2).replace("\n", "\n  ") if s["output"] else f"  error: {s['error']}")
    a = store.approval(rid)
    if a:
        print(f"\n[gate] {a['decision']} by {a['approver']}{' (OVERRIDE)' if a['override'] else ''}: {a['reason']}")
    op = store.operation(rid)
    if op:
        print(f"\n[apply] {op['status']} · op {op['op_id']} · {op['action']} {json.dumps(op['payload'])}")

class ReplayRunner:
    """Plays saved stage outputs back through the real stages, contracts and gate. No model, no cost."""
    def __init__(self, fixture):
        self.stages = fixture["stages"]
    def run(self, stage, system, prompt, schema):
        saved = self.stages[stage]
        return agents.AgentResult(json.loads(json.dumps(saved["output"])), 0.0, [], 0)

def replay(store, path):
    """A real, saved run - e.g. one the reviewer BLOCKED - so every learner gets the same case to decide."""
    fx = json.loads(Path(path).read_text(encoding="utf-8"))
    rid = store.create_run(fx["account"], fx["question"] + f"  [replay of {fx['source_run']}]")
    advance(store, ReplayRunner(fx), rid)
    return rid

def issue_tokens(account):
    ops = HERE.parents[1] / "day3-integration-security" / "aira-ops" / "aira_ops.py"
    callers = ops.parent / "callers.json"
    def issue(actor, *extra):
        return subprocess.run([sys.executable, str(ops), "--callers", str(callers), "--issue-token", actor, *extra],
                              capture_output=True, text=True, check=True, env=dict(os.environ, AIRA_OPS_TOKEN=os.environ.get("AIRA_OPS_TOKEN", "x"))).stdout.strip()
    rd = issue("pipeline-agents", "--accounts", account)
    wr = issue("pipeline-apply", "--write")
    print("# Tokens are shown once; callers.json keeps only their hashes. Restart aira-ops with --callers.")
    print(f"export AIRA_OPS_READ_TOKEN={rd}\nexport AIRA_OPS_APPLY_TOKEN={wr}")
    # Absolute paths: pasted from any folder, aira-ops must load THIS callers.json, or every agent call is refused.
    print(f'# then restart aira-ops (same AIRA_OPS_TOKEN as before):\n#   python3 "{ops}" --callers "{callers}"')

def main():
    ap = argparse.ArgumentParser(description="Lab 5.1 pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("--account", required=True); p.add_argument("--question", required=True)
    for c in ("show", "apply", "resume"):
        sub.add_parser(c).add_argument("run")
    for c in ("approve", "reject"):
        p = sub.add_parser(c); p.add_argument("run"); p.add_argument("--by", required=True); p.add_argument("--reason", required=True)
        p.add_argument("--override", action="store_true")
    sub.add_parser("list")
    sub.add_parser("replay").add_argument("fixture")
    p = sub.add_parser("tokens"); p.add_argument("--account", required=True)
    a = ap.parse_args()
    if a.cmd == "tokens":
        return issue_tokens(a.account)
    store = Store(os.environ.get("PIPELINE_DB", str(HERE / "runs.sqlite")))
    # Least privilege per PROCESS: commands that start agents drop the write and admin tokens from
    # this process's environment first (the SDK hands the whole environment to the agent's
    # subprocess). Only `apply` - which starts no agent - keeps the write token.
    write_tok = os.environ.pop("AIRA_OPS_APPLY_TOKEN", "") if a.cmd == "apply" else ""
    read_tok = os.environ.get("AIRA_OPS_READ_TOKEN", "")   # handed to the MCP server explicitly
    if a.cmd in ("run", "resume"):
        agents.scrub_agent_environment()
    if a.cmd in ("run", "resume") and not read_tok:
        raise SystemExit("AIRA_OPS_READ_TOKEN is not set - agents get a read-only caller token (python3 pipeline.py tokens)")
    try:
        if a.cmd == "run":
            runner = agents.SdkRunner(OPS_URL, read_tok)
            rid = store.create_run(a.account, a.question)
            print(f"run {rid} started")
            advance(store, runner, rid); show(store, rid)
        elif a.cmd == "show":
            show(store, a.run)
        elif a.cmd in ("approve", "reject"):
            decide(store, a.run, a.cmd, a.by, a.reason, a.override); show(store, a.run)
        elif a.cmd == "apply":
            if not write_tok:
                raise SystemExit("AIRA_OPS_APPLY_TOKEN is not set - the apply step has its own credential (python3 pipeline.py tokens)")
            apply(store, a.run, write_tok); show(store, a.run)
        elif a.cmd == "resume":
            resume(store, agents.SdkRunner(OPS_URL, read_tok), a.run); show(store, a.run)
            if store.run(a.run)["status"] in ("approved", "outcome_unknown"):
                print(f"next: python3 pipeline.py apply {a.run}   (in a shell that holds AIRA_OPS_APPLY_TOKEN)")
        elif a.cmd == "replay":
            rid = replay(store, a.fixture); print(f"run {rid} replayed from {a.fixture}"); show(store, rid)
        elif a.cmd == "list":
            for r in store.runs():
                print(f"{r['id']}  {r['account_id']}  {r['status']:<18} ${store.cost(r['id']):<7} {r['question'][:60]}")
    except (GateError, ContractError, KeyError, Conflict) as e:
        raise SystemExit(f"refused: {e}")
    except agents.RunnerError as e:
        rid = getattr(a, "run", None) or locals().get("rid")
        raise SystemExit(f"stage failed (recorded, finished stages kept): {e}\n"
                         f"  trace:  python3 ../common/trace_view.py --latest lab5-1-{rid}\n"
                         f"  retry:  python3 pipeline.py resume {rid}")

if __name__ == "__main__":
    main()
