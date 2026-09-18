#!/usr/bin/env python3
"""Measure what a tool schema is worth.

Runs the same tasks twice - once with whatever schema file you point it at - and
reports success, steps and cost. Success is judged by what changed in the ticket
store, never by what the agent claimed.

    python3 score.py schemas/bad.json
    python3 score.py schemas/yours.json
    python3 score.py schemas/bad.json schemas/yours.json     # compare
"""
import json, pathlib, sys

for _parent in pathlib.Path(__file__).resolve().parents:
    if (_parent / "labkit" / "python").is_dir():
        sys.path.insert(0, str(_parent / "labkit" / "python"))
        break
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agentic_core import GatewayClient, Config, BudgetGuard          # noqa: E402
from tickets import Store                                            # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
SYSTEM = ("You are a ticket triage assistant. Use the tools to make the change "
          "the user asks for. Do not claim you have made a change you did not make.")
MAX_STEPS = 6


def run_task(client, schemas, goal, budget):
    store = Store()
    messages = [{"role": "user", "content": goal}]
    for step in range(1, MAX_STEPS + 1):
        response = client.messages(messages, tools=schemas, system=SYSTEM, max_tokens=800)
        budget.record(response.get("usage", {}))
        blocks = response.get("content", [])
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if response.get("stop_reason") != "tool_use":
            return store, step
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for call in calls:
            out, ok = store.dispatch(call["name"], call.get("input", {}))
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": out, "is_error": not ok})
        messages.append({"role": "user", "content": results})
    return store, MAX_STEPS


def score(path: pathlib.Path, tasks, client, budget):
    schemas = json.loads(path.read_text())
    passed, steps_total, errors = 0, 0, 0
    print(f"\n{path.name}")
    for task in tasks:
        store, steps = run_task(client, schemas, task["goal"], budget)
        want = task["expect"]
        got = store.tickets[want["ticket"]][want["field"]]
        ok = got == want["value"]
        bad_calls = sum(1 for name, args in store.calls
                        if name == "set_priority" and args.get("priority") not in
                        ["low", "medium", "high", "critical"])
        passed += ok
        steps_total += steps
        errors += bad_calls
        print(f"  {task['id']}  {'PASS' if ok else 'FAIL'}  "
              f"steps={steps}  wanted={want['value']!r} got={got!r}"
              + (f"  [{bad_calls} invalid value(s) attempted]" if bad_calls else ""))
    n = len(tasks)
    print(f"  ── {passed}/{n} passed · {steps_total/n:.1f} steps avg · "
          f"{errors} invalid tool arguments")
    return passed, steps_total / n, errors


def main() -> None:
    paths = [pathlib.Path(a) for a in sys.argv[1:]] or [HERE / "schemas" / "yours.json"]
    tasks = [json.loads(line) for line in (HERE / "tasks.jsonl").read_text().splitlines() if line.strip()]
    cfg = Config()
    client = GatewayClient(cfg)
    budget = BudgetGuard(cfg.budget_usd, cfg.model)
    results = {}
    for path in paths:
        if not path.exists():
            raise SystemExit(f"no such schema file: {path}")
        results[path.name] = score(path, tasks, client, budget)
    print(f"\nspend: {budget.summary()}")
    if len(results) > 1:
        print("\n  schema file            passed   avg steps   invalid args")
        for name, (passed, steps, errors) in results.items():
            print(f"  {name:<22} {passed}/{len(tasks)}      {steps:>4.1f}        {errors}")
        print("\nThe implementations are identical. Only the descriptions changed.")


if __name__ == "__main__":
    main()
