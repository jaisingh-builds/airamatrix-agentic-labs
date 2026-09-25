#!/usr/bin/env python3
"""
Lab 5.2 - run the golden set against the investigation agent and gate on it.

    python3 run_evals.py                          # every case once
    python3 run_evals.py --repeat 3 --cases backlog-cause,injection-t1007   # consistency
    python3 run_evals.py --regrade results/eval-XXXX.json    # re-grade saved runs, no model, free
    python3 run_evals.py                          # also the CI gate: the threshold is frozen in the
                                                  # golden file; CI refuses --min-pass (local experiments only)

Exit codes: 0 gate passed · 1 gate failed · 2 could not run (setup error, budget).
Each run gets a private aira-ops with fresh data and a read-only caller token,
so cases can't leak into each other and no case can change anything.
"""
import argparse, concurrent.futures as cf, json, os, secrets, socket, subprocess, sys, tempfile, time, urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB51 = HERE.parent / "lab5-1-handoff"
OPS = HERE.parents[1] / "day3-integration-security" / "aira-ops" / "aira_ops.py"
# LAB52_TARGET=starter grades with YOUR starter/graders.py, so the live run checks your code too.
GRADERS = HERE / "starter" if os.environ.get("LAB52_TARGET") == "starter" else HERE
for p in (LAB51, HERE.parent / "common", GRADERS):
    sys.path.insert(0, str(p))
from graders import grade_case, gate  # noqa: E402

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Ops:
    """A throwaway aira-ops with a read-only caller token for the agent."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        admin = "eval-admin-" + secrets.token_hex(8)
        env = dict(os.environ, AIRA_OPS_TOKEN=admin)
        callers = Path(self.tmp.name) / "callers.json"
        self.read_token = subprocess.run([sys.executable, str(OPS), "--callers", str(callers), "--issue-token", "eval-agent"],
                                         capture_output=True, text=True, env=env, check=True).stdout.strip()
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self.p = subprocess.Popen([sys.executable, str(OPS), "--port", str(port), "--quiet", "--reset",
                                   "--db", str(Path(self.tmp.name) / "ops.sqlite"), "--callers", str(callers)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(self.url + "/health", timeout=0.3); return self
            except Exception: time.sleep(0.1)
        raise RuntimeError("aira-ops did not start")
    def __exit__(self, *a):
        self.p.terminate(); self.p.wait(); self.tmp.cleanup()

def run_one(case, ops, max_budget):
    import agents
    from contracts import PROPOSAL, validate, check_change
    from spans import Tracer
    tr = Tracer("lab5-2", trace_id=f"{case['id']}-{secrets.token_hex(3)}")
    t0 = time.time()
    try:
        with tr.span("eval.case", case=case["id"]) as sp:
            r = agents.SdkRunner(ops.url, ops.read_token, max_budget_usd=max_budget).run(
                "investigate", agents.INVESTIGATE_SYSTEM, agents.investigate_prompt(case["account"], case["question"]), PROPOSAL)
            check_change(validate(r.output, PROPOSAL)["proposed_change"])
            oks = r.tool_ok or [None] * len(r.tool_calls)
            for (name, args), ok in zip(r.tool_calls, oks):
                tr.event("tool_call", tool=name, input=args, ok=ok)
            sp.set(cost_usd=round(r.cost_usd, 4), tool_calls=len(r.tool_calls))
        raw = {"output": r.output, "tool_calls": [[n, a, ok] for (n, a), ok in zip(r.tool_calls, oks)]}
        return {"raw": raw, "grade": grade_case(case, raw), "cost_usd": r.cost_usd,
                "seconds": round(time.time() - t0, 1), "trace": tr.path.name}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}", "cost_usd": getattr(e, "cost_usd", 0.0) or 0.0,
                "seconds": round(time.time() - t0, 1), "trace": tr.path.name}

def report(suite, case_results, g, total_cost, out):
    lines = [f"## Eval gate: {'PASS' if g['ok'] else 'FAIL'} — {suite}", "",
             f"{g['passed']}/{g['runs']} runs passed ({g['pass_rate']:.0%}, need {g['min_pass_rate']:.0%}) · "
             f"first attempt {g['first_attempt_passed']}/{g['runs']} · {g['retried']} retried after an error · "
             f"{g['unrecovered_errors']} unrecovered errors · ${total_cost:.2f}", "",
             "A run = one attempt + at most one retry for an execution/schema error. An unrecovered error "
             "or a failed critical check blocks the gate.", "",
             "| case | runs passed | failing checks |", "|---|---|---|"]
    for c in case_results:
        ok = sum(1 for r in c["runs"] if r.get("grade", {}).get("passed"))
        fails = sorted({f"{'**' if ch['critical'] else ''}{ch['check']}{'**' if ch['critical'] else ''}: {ch['detail']}"
                        for r in c["runs"] for ch in r.get("grade", {}).get("checks", []) if not ch["passed"]}
                       | {f"error: {r['error'][:80]}" for r in c["runs"] if "error" in r})
        flaky = " (flaky)" if 0 < ok < len(c["runs"]) else ""
        lines.append(f"| {c['id']} | {ok}/{len(c['runs'])}{flaky} | {'<br>'.join(fails) or '—'} |")
    if g["critical_failures"]:
        lines += ["", "**Critical checks failed** — a safety property is never averaged away:"]
        lines += [f"- `{cid}`: {d}" for cid, d in g["critical_failures"]]
    md = "\n".join(lines)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n")
    return md

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(HERE / "golden" / "cases.json"))
    ap.add_argument("--cases", help="comma-separated case ids")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-pass", type=float, help="override the threshold frozen in the golden file (local experiments only)")
    ap.add_argument("--retry-errors", type=int, default=1, help="re-run a run that ERRORED (not one that failed) this many times")
    ap.add_argument("--budget", type=float, default=float(os.environ.get("EVAL_BUDGET_USD", 2.0)))
    ap.add_argument("--per-run-budget", type=float, default=0.40)
    ap.add_argument("--regrade", metavar="RESULTS_JSON")
    a = ap.parse_args()
    golden = json.loads(Path(a.golden).read_text(encoding="utf-8"))
    frozen = golden["gate"]["min_pass_rate"]
    if a.min_pass is not None and a.min_pass != frozen:
        if os.environ.get("CI"):
            print(f"setup: --min-pass {a.min_pass} would override the frozen {frozen}; change golden file instead", file=sys.stderr)
            return 2
        print(f"warning: overriding the frozen threshold {frozen} with {a.min_pass} (local only)", file=sys.stderr)
    a.min_pass = a.min_pass if a.min_pass is not None else frozen
    cases = [c for c in golden["cases"] if not a.cases or c["id"] in a.cases.split(",")]
    has_critical = {c["id"]: any(ch.get("critical") for ch in c["checks"]) for c in cases}
    if not cases:
        print("no matching cases", file=sys.stderr); return 2

    if a.regrade:                                  # grader development: no model, no cost
        prev = json.loads(Path(a.regrade).read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in cases}
        results = [{"id": c["id"], "has_critical": has_critical[c["id"]],
                    "runs": [dict(r, grade=grade_case(by_id[c["id"]], r["raw"])) if "raw" in r else r
                             for r in c["runs"]]} for c in prev["cases"] if c["id"] in by_id]
        g = gate(results, a.min_pass)
        report(golden["suite"] + " (re-graded)", results, g, 0.0, None)
        return 0 if g["ok"] else 1

    try:
        sys.path.insert(0, str(HERE.parents[1] / "labkit" / "python"))
        from agentic_core import Config
        Config().require()
    except SystemExit as e:
        print(f"setup: {e}", file=sys.stderr); return 2
    results = {c["id"]: {"id": c["id"], "has_critical": has_critical[c["id"]], "runs": []} for c in cases}
    agents_mod = __import__("agents")
    agents_mod.scrub_agent_environment()        # this process starts agents: nothing secret but the gateway key
    spent = 0.0
    with Ops() as ops, cf.ThreadPoolExecutor(a.workers) as pool:
        jobs = {}
        for rep in range(a.repeat):
            for c in cases:
                jobs[pool.submit(run_one, c, ops, a.per_run_budget)] = c["id"]
        for fut in cf.as_completed(jobs):
            r = fut.result()
            case = next(c for c in cases if c["id"] == jobs[fut])
            for _ in range(a.retry_errors):          # an ERROR (no valid output) is retried; a FAIL never is
                if "error" not in r:
                    break
                spent += r["cost_usd"]
                print(f"  RETRY {jobs[fut]:<22} after: {r['error'][:70]}", flush=True)
                r = dict(run_one(case, ops, a.per_run_budget), retried_after=r["error"][:200], errored_cost_usd=r["cost_usd"])
            spent += r["cost_usd"]
            results[jobs[fut]]["runs"].append(r)
            status = "PASS" if r.get("grade", {}).get("passed") else ("ERROR" if "error" in r else "FAIL")
            print(f"  {status:5} {jobs[fut]:<22} ${r['cost_usd']:.3f} {r['seconds']}s", flush=True)
            if spent > a.budget:
                print(f"budget ${a.budget} exceeded (${spent:.2f}) - cancelling the rest", file=sys.stderr)
                for f in jobs:
                    f.cancel()
    case_results = [results[c["id"]] for c in cases]
    g = gate(case_results, a.min_pass)
    out = HERE / "results" / f"eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    from spans import redact
    # results are kept for --regrade: secrets masked (not truncated - the grader needs the full text)
    out.write_text(json.dumps(redact({"suite": golden["suite"], "gate": g, "cost_usd": round(spent, 4),
                                      "cases": case_results}, limit=None), indent=2), encoding="utf-8")
    report(golden["suite"], case_results, g, spent, out)
    print(f"\nresults: {out.relative_to(HERE)}")
    if g["errors"] == g["runs"]:
        return 2
    return 0 if g["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
