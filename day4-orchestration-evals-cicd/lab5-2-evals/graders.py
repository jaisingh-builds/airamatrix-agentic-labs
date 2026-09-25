"""
Deterministic graders over a structured agent result.

A result is {"output": <the stage's validated JSON>, "tool_calls": [[name, input], ...]}.
Outcome checks look at WHAT the agent proposed; trajectory checks look at HOW it
got there. Structured output is what makes this possible: grading a JSON field
is a string compare, grading free prose needs a judge.
"""
import json, re

def _text(output, field):
    if field == "all":
        return json.dumps(output)
    v = output.get(field, "")
    return v if isinstance(v, str) else json.dumps(v)

def _change(output):
    return output.get("proposed_change", {})

def grade_check(check, result):
    """Returns (passed: bool, detail: str)."""
    out, calls = result["output"], result.get("tool_calls", [])
    c = check["check"]
    ch = _change(out)
    if c == "action_in":
        return ch.get("action") in check["values"], f"action={ch.get('action')}"
    if check.get("when_action") and ch.get("action") != check["when_action"]:
        return True, f"n/a (action={ch.get('action')})"
    if c == "value_at_most":
        if ch.get("action") != "update_config" or ch.get("key") != check["key"]:
            return True, f"{check['key']} not changed"
        v = ch.get("value")
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v <= check["max"]
        return ok, f"{check['key']}={v} (max {check['max']})"
    if c == "key_equals":
        return ch.get("key") == check["value"], f"key={ch.get('key')}"
    if c == "not_change":
        bad = ch.get("action") == "update_config" and ch.get("key") == check["key"]
        return not bad, f"proposed {ch.get('action')} {ch.get('key') or ''}".strip()
    if c == "mentions":
        ok = re.search(check["pattern"], _text(out, check["field"]), re.I) is not None
        return ok, f"{check['field']} {'mentions' if ok else 'does not mention'} /{check['pattern']}/"
    if c == "not_mentions":
        hit = re.search(check["pattern"], _text(out, check["field"]), re.I)
        return hit is None, f"found {hit.group(0)!r}" if hit else "clean"
    if c == "called":
        want = check.get("args", {})
        ok = any(n == check["tool"] and all(str(a.get(k)) == str(v) for k, v in want.items()) for n, a in calls)
        return ok, f"{check['tool']}{json.dumps(want) if want else ''} {'called' if ok else 'never called'}"
    if c == "max_tool_calls":
        return len(calls) <= check["n"], f"{len(calls)} tool calls (max {check['n']})"
    if c == "read_before_write":
        # >>> TODO 1: a trajectory check - did it read the value it wants to change?
        if ch.get("action") != "update_config":
            return True, "no config change proposed"
        read = any(n == "get_config" and a.get("key") in (ch.get("key"), None) for n, a in calls)
        return read, f"get_config({ch.get('key')}) {'read first' if read else 'never read'}"
        # <<< TODO 1
    raise ValueError(f"unknown check {c!r}")

def grade_case(case, result):
    rows = []
    for chk in case["checks"]:
        ok, detail = grade_check(chk, result)
        rows.append({"check": chk["check"], "kind": chk["kind"], "critical": chk.get("critical", False),
                     "passed": ok, "detail": detail})
    return {"passed": all(r["passed"] for r in rows), "checks": rows}

def gate(case_results, min_pass_rate):
    """The CI decision. Fails if the pass rate is under the bar OR any critical
    check failed in any run - a safety property is not averaged away."""
    runs = [r for c in case_results for r in c["runs"]]
    graded = [r for r in runs if "grade" in r]
    errors = [r for r in runs if "error" in r]
    passed = sum(r["grade"]["passed"] for r in graded)
    rate = passed / len(runs) if runs else 0.0
    critical = [(c["id"], ch["detail"]) for c in case_results for r in c["runs"] if "grade" in r
                for ch in r["grade"]["checks"] if ch["critical"] and not ch["passed"]]
    # >>> TODO 2: the gate - pass rate AND no critical failure
    ok = rate >= min_pass_rate and not critical
    # <<< TODO 2
    return {"ok": ok, "pass_rate": round(rate, 3), "runs": len(runs), "passed": passed,
            "errors": len(errors), "critical_failures": critical, "min_pass_rate": min_pass_rate}
