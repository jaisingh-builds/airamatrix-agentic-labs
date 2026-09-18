#!/usr/bin/env python3
"""Lab 1.2 reference - the tool schema design clinic.

Slide 34. Same loop, same tasks, same model. The ONLY thing that changes is the
tool contract the model is handed: names, descriptions and JSON Schema.

    python3 clinic.py --workspace ../../../../airamatrix-agentic-labs
    python3 clinic.py --workspace DIR --schema schemas/bad.json     # one only

What it measures, per schema set:

  passed              did the answer contain the verified ground truth
  steps               model calls needed
  invalid arguments   tool calls the executor had to refuse - wrong parameter,
                      unusable value, wrong host. THIS is what a vague contract
                      actually costs you.
  unknown tool        the model invented a tool that does not exist
  cost                real money

Ground truth is fetched live where it can change, so the numbers are never stale.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# the Lab 1.1 reference loop lives next door - reuse it, do not reimplement it
_here = Path(__file__).resolve().parent
for _c in (_here, *_here.parents):
    _a = _c / "lab1-bare-metal-loop" / "reference"
    if _a.is_dir():
        sys.path.insert(0, str(_a))
        break
import agent as A  # noqa: E402

REPO = "anthropics/anthropic-sdk-python"


# --------------------------------------------------------------- ground truth
def _gh(path: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com{path}",
                                 headers={"user-agent": "lab12-clinic",
                                          "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def build_tasks() -> list[dict]:
    """Five tasks. Expected values that can drift are fetched live."""
    meta = _gh(f"/repos/{REPO}")
    return [
        {"id": "t1", "needs": "read_file",
         "goal": "Which Java version does this project target? Check pom.xml.",
         "expect": "21"},
        {"id": "t2", "needs": "http_get",
         "goal": f"What is the default branch of the {REPO} repository on GitHub?",
         "expect": meta["default_branch"]},
        {"id": "t3", "needs": "http_get",
         "goal": f"What licence does the {REPO} repository use? Give the SPDX id.",
         "expect": (meta.get("license") or {}).get("spdx_id", "MIT")},
        {"id": "t4", "needs": "calculator",
         "goal": "A queue is at depth 812 against a limit of 500. By what percentage "
                 "does it exceed the limit? Give one decimal place.",
         "expect": "62.4"},
        {"id": "t5", "needs": "http_get+calculator",
         # name the exact JSON field - "open issues" is ambiguous on GitHub
         # because open_issues_count includes pull requests.
         "goal": f"Fetch the repository metadata for {REPO} from the GitHub API and "
                 f"read the `forks_count` field. If 10 forks were deleted, how many "
                 f"would remain? Give just the number.",
         "expect": str(max(0, meta["forks_count"] - 10))},
    ]


# ------------------------------------------------- run one task, one contract
def run_task(goal: str, tools: list[dict], workspace: Path) -> dict:
    """A trimmed copy of agent.run() that counts what we care about."""
    A.WORKSPACE = workspace
    impl = {t["name"]: A.REGISTRY[t["impl"]]["fn"] for t in tools}
    wire = [{k: t[k] for k in ("name", "description", "input_schema")} for t in tools]

    budget = A.Budget(A.MAX_COST_USD, A.DEADLINE_SECONDS)
    messages = [{"role": "user", "content": goal}]
    stats = {"steps": 0, "invalid_args": 0, "unknown_tool": 0, "refused": 0}

    for step in range(1, A.MAX_STEPS + 1):
        ok, _ = budget.reserve()
        if not ok:
            return {**stats, "answer": "", "cost": budget.spent_usd, "stopped": "budget"}

        payload = json.dumps({"model": A.MODEL, "max_tokens": 2048,
                              "system": A.SYSTEM_PROMPT, "tools": wire,
                              "messages": messages}).encode()
        req = urllib.request.Request(
            f"{A.BASE_URL}/v1/messages", data=payload, method="POST",
            headers={"content-type": "application/json",
                     "anthropic-version": "2023-06-01",
                     "authorization": f"Bearer {A.AUTH_TOKEN}"})
        with urllib.request.urlopen(req, timeout=90) as r:
            reply = json.loads(r.read())

        budget.record(reply.get("usage", {}))
        stats["steps"] = step
        stop = reply.get("stop_reason")

        if stop == "tool_use":
            messages.append({"role": "assistant", "content": reply["content"]})
            results = []
            for b in reply["content"]:
                if b["type"] != "tool_use":
                    continue
                res = {"type": "tool_result", "tool_use_id": b["id"]}
                fn = impl.get(b["name"])
                if fn is None:
                    stats["unknown_tool"] += 1
                    res["content"] = f"Unknown tool {b['name']!r}. Have: {', '.join(impl)}"
                    res["is_error"] = True
                else:
                    try:
                        res["content"] = fn(b.get("input", {}))
                    except KeyError as e:
                        # the model passed an argument the implementation cannot use
                        stats["invalid_args"] += 1
                        res["content"] = (f"Missing or unusable argument {e}. "
                                          f"Got: {json.dumps(b.get('input', {}))[:120]}")
                        res["is_error"] = True
                    except A.PolicyRefusal as e:
                        stats["refused"] += 1
                        res["content"] = str(e)
                        res["is_error"] = True
                    except A.ToolError as e:
                        stats["invalid_args"] += 1
                        res["content"] = str(e)
                        res["is_error"] = True
                results.append(res)
            messages.append({"role": "user", "content": results})
            continue

        text = "".join(b.get("text", "") for b in reply["content"])
        return {**stats, "answer": text, "cost": budget.spent_usd, "stopped": stop}

    return {**stats, "answer": "", "cost": budget.spent_usd, "stopped": "step_cap"}


def score(schema_path: Path, tasks: list[dict], workspace: Path) -> dict:
    tools = json.loads(schema_path.read_text())
    print(f"\n\033[1m{schema_path.name}\033[0m")
    agg = {"passed": 0, "steps": 0, "invalid_args": 0, "unknown_tool": 0,
           "refused": 0, "cost": 0.0}

    for t in tasks:
        r = run_task(t["goal"], tools, workspace)
        # pass = the verified ground truth appears in the answer
        got = re.sub(r"[^\w.\-]", " ", r["answer"] or "")
        ok = t["expect"].lower() in got.lower()
        agg["passed"] += ok
        for k in ("steps", "invalid_args", "unknown_tool", "refused"):
            agg[k] += r[k]
        agg["cost"] += r["cost"]

        mark = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
        notes = []
        if r["invalid_args"]:  notes.append(f"{r['invalid_args']} invalid arg")
        if r["unknown_tool"]:  notes.append(f"{r['unknown_tool']} unknown tool")
        if r["refused"]:       notes.append(f"{r['refused']} refused")
        if r["stopped"] not in ("end_turn",): notes.append(f"stopped: {r['stopped']}")
        print(f"  {t['id']}  {mark}  steps={r['steps']}  want={t['expect']!r:<10}"
              f" {'· ' + ', '.join(notes) if notes else ''}")

    n = len(tasks)
    print(f"  ── {agg['passed']}/{n} passed · {agg['steps']/n:.1f} steps avg · "
          f"{agg['invalid_args']} invalid args · {agg['unknown_tool']} unknown tool · "
          f"${agg['cost']:.4f}")
    return agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=str(A.WORKSPACE))
    ap.add_argument("--schema", action="append",
                    help="repeatable; default runs schemas/bad.json then schemas/good.json")
    args = ap.parse_args()

    if not A.BASE_URL or not A.AUTH_TOKEN:
        sys.exit("set ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    here = Path(__file__).parent
    ws = Path(args.workspace).resolve()
    paths = [Path(s) for s in (args.schema or
                               [here / "schemas/bad.json", here / "schemas/good.json"])]
    print(f"workspace : {ws}\nmodel     : {A.MODEL}")

    tasks = build_tasks()
    print(f"tasks     : {len(tasks)}, ground truth fetched live")

    results = {p.name: score(p, tasks, ws) for p in paths}

    if len(results) == 2:
        (an, a), (bn, b) = results.items()
        n = len(tasks)
        print(f"\n\033[1mWhat the contract was worth\033[0m")
        print(f"  {'':<16}{an:>14}{bn:>14}")
        for label, key, fmt in (("passed", "passed", "{}/%d" % n),
                                ("steps (avg)", "steps", "{:.1f}"),
                                ("invalid args", "invalid_args", "{}"),
                                ("unknown tool", "unknown_tool", "{}"),
                                ("cost", "cost", "${:.4f}")):
            av = a[key] / n if key == "steps" else a[key]
            bv = b[key] / n if key == "steps" else b[key]
            print(f"  {label:<16}{fmt.format(av):>14}{fmt.format(bv):>14}")
        if a["passed"] == b["passed"]:
            print(f"\n  Both contracts answered {a['passed']}/{n}. The difference is "
                  f"not correctness -\n  it is steps, retries and money. That is why "
                  f"nobody ever fixes a tool description.")


if __name__ == "__main__":
    main()
