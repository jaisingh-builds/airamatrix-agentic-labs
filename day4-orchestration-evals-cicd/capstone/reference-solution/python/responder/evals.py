"""The eval harness: every golden case x repeats, graded by checks.py, gated, written to a results file
and a Markdown report. A RUN is one attempt plus at most one retry after an ERROR (no proposal) - never
after a FAIL. The first-attempt pass count is reported beside the final one, so a retry cannot hide flakiness.

Targets: local (a private aira-ops per eval with fresh seed data + the training gateway) or, from
agentcore/capstone_aws.py, the deployed AgentCore runtime. Grading is identical.

Exit codes: 0 gate passed, 1 gate failed, 2 could not run.
"""
import json, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import checks, responder
from .ops import HttpOpsReader, hex_
from .repo import spans
from .util import cut, jfmt, jround, parse_instant


def load(path):
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    for c in g.get("cases", []):
        for chk in c.get("checks", []):
            if chk.get("check") not in checks.KNOWN:
                raise ValueError(f"{c.get('id')}: unknown check {chk.get('check')}")
    return g


def select(golden, ids):
    want = [x for x in (ids or "").split(",")] if ids and ids.strip() else None
    return [c for c in golden.get("cases", []) if want is None or c.get("id") in want]


def accounts(cases):
    return sorted({c["account"] for c in cases})


def execute(golden, cases, repeat, workers, budget, target, runner, out_dir, out):
    """Runs, grades, gates, writes results + report. runner(case) -> result dict or {error, cost_usd, trace}; never raises."""
    min_rate = float(golden.get("gate", {}).get("min_pass_rate", 1.0))
    by_id = {}
    for c in cases:
        by_id[c["id"]] = {"id": c["id"], "has_critical": any(ch.get("critical") for ch in c.get("checks", [])), "runs": []}
    spent = [0.0]
    lock = threading.Lock()

    def job(c):
        with lock:
            if spent[0] > budget:
                return
        r = runner(c)
        if "error" in r:                                        # an ERROR is retried once; a FAIL never is
            with lock:
                spent[0] += r.get("cost_usd", 0.0)
                out.write(f"  RETRY {c['id']:<26} after: {cut(r['error'], 70)}\n")
            again = runner(c)
            again["retried_after"] = cut(r["error"], 200)
            again["errored_cost_usd"] = r.get("cost_usd", 0.0)
            r = again
        if "error" not in r:
            r["grade"] = checks.grade_case(c, r)
        with lock:
            spent[0] += r.get("cost_usd", 0.0)
            by_id[c["id"]]["runs"].append(r)
            st = "ERROR" if "error" in r else "PASS" if r["grade"]["passed"] else "FAIL"
            out.write(f"  {st:<5} {c['id']:<26} ${jfmt(r.get('cost_usd', 0.0), 3)}  {r.get('status', '')}\n")
            out.flush()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, c) for _ in range(repeat) for c in cases]
        for f in futures:
            f.result()
    results = list(by_id.values())
    gate = checks.gate(results, min_rate)
    if spent[0] > budget:
        gate["budget_exceeded"] = True
    doc = {"suite": golden.get("suite", ""), "target": target, "cost_usd": jround(spent[0], 4), "gate": gate,
           "cases": results}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath, mpath = out_dir / f"eval-{target}-{stamp}.json", out_dir / f"eval-{target}-{stamp}.md"
    # secrets masked on save, not truncated (--regrade needs the full text)
    jpath.write_text(json.dumps(spans.redact(doc, limit=None), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rep = report(doc)
    mpath.write_text(rep, encoding="utf-8")
    out.write("\n" + rep + "\n")
    out.write(f"results: {jpath}\n")
    if gate["runs"] == gate["unrecovered_errors"]:
        return 2
    return 0 if gate["ok"] else 1


def regrade(golden, saved, out):
    """Re-grade a saved results file with the current golden checks: no model, no cost."""
    prev = json.loads(Path(saved).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in golden.get("cases", [])}
    results = []
    for c in prev.get("cases", []):
        kase = by_id.get(c.get("id"))
        if kase is None:
            continue
        cc = dict(c, runs=[])
        for r in c.get("runs", []):
            rr = dict(r)
            if "error" not in rr:
                rr["grade"] = checks.grade_case(kase, rr)
            cc["runs"].append(rr)
        results.append(cc)
    gate = checks.gate(results, float(golden.get("gate", {}).get("min_pass_rate", 1.0)))
    doc = {"suite": golden.get("suite", "") + " (re-graded)", "target": prev.get("target", ""), "cost_usd": 0.0,
           "gate": gate, "cases": results}
    out.write(report(doc) + "\n")
    return 0 if gate["ok"] else 1


def report(doc):
    g = doc["gate"]
    lines = [f"## Eval gate: {'PASS' if g['ok'] else 'FAIL'} - {doc['suite']} (target: {doc['target']})", "",
             f"{g['passed']}/{g['runs']} runs passed ({jfmt(100 * g['pass_rate'], 0)}%, need "
             f"{jfmt(100 * g['min_pass_rate'], 0)}%) · first attempt {g['first_attempt_passed']}/{g['runs']} · "
             f"{g['retried']} retried after an error · {g['unrecovered_errors']} unrecovered errors · "
             f"${jfmt(doc['cost_usd'], 2)}", "",
             "| case | runs passed | failing checks |", "|---|---|---|"]
    for c in doc["cases"]:
        ok, n, fails = 0, len(c["runs"]), set()
        for r in c["runs"]:
            if "error" in r:
                fails.add("error: " + cut(r["error"], 80))
                continue
            if r["grade"]["passed"]:
                ok += 1
            for ch in r["grade"]["checks"]:
                if not ch["passed"]:
                    name = f"**{ch['check']}**" if ch["critical"] else ch["check"]
                    fails.add(f"{name}: {ch['detail']}")
        flaky = " (flaky)" if 0 < ok < n else ""
        lines.append(f"| {c['id']} | {ok}/{n}{flaky} | {'<br>'.join(sorted(fails)) if fails else '—'} |")
    if g["critical_failures"]:
        lines += ["", "**Critical checks failed** - a safety property is never averaged away:"]
        lines += [f"- {f}" for f in g["critical_failures"]]
    return "\n".join(lines) + "\n"


def local_runner(ops, make_agent, per_run_budget):
    """The local target: one private aira-ops, a read token per account, the training gateway for the model."""
    from .repo import spans as _spans

    def run(kase):
        rid = f"eval-{kase['id']}-{hex_(3)}"
        tr = _spans.Tracer("capstone", trace_id=rid)
        t0 = time.monotonic()
        try:
            acc = kase["account"]
            reader = HttpOpsReader(ops.url, ops.read_tokens[acc])
            o = responder.run(rid, acc, parse_instant(kase["as_of"]), kase.get("question", ""), reader,
                              make_agent(per_run_budget), tr)
            if o.status in ("failed", "guardrail_intervened"):
                return {"error": o.error, "cost_usd": o.cost_usd, "trace": tr.path.name}
            r = o.to_json()
            r.pop("sla", None)
            r["seconds"] = jround(time.monotonic() - t0, 1)
            r["trace"] = tr.path.name
            return r
        except Exception as e:
            return {"error": f"{type(e).__name__}: {cut(str(e), 300)}", "cost_usd": 0.0, "trace": tr.path.name}
    return run
