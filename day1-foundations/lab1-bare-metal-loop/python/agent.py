#!/usr/bin/env python3
"""Lab 1.1 — Build an agent loop from scratch. No framework.

You are writing the loop. That is the whole exercise: everything in the rest of
the programme sits on top of the cycle you are about to implement.

    observe -> decide -> act -> observe

Run it:   python3 agent.py
Check it: python3 test_agent.py

This is the worked solution (branch `solutions`). The starter, with the five
TODOs, is on `main`. Every stop_reason has its own branch: see run_agent below.
"""
import json, pathlib, sys

# Find labkit/ by walking up - keeps this file working wherever it lives.
for _parent in pathlib.Path(__file__).resolve().parents:
    if (_parent / "labkit" / "python").is_dir():
        sys.path.insert(0, str(_parent / "labkit" / "python"))
        break

from agentic_core import GatewayClient, Config, Tracer, BudgetGuard, BudgetExceeded  # noqa: E402
import tools as lab_tools                                                            # noqa: E402

SYSTEM = (
    "You are an operations assistant. Use the provided tools to gather facts "
    "before answering. Never guess a number you could compute with the calculator, "
    "and never invent file contents. When you have the answer, state it plainly."
)


class StepLimitExceeded(RuntimeError):
    """Raised when the agent burns its step budget without finishing."""


class Truncated(RuntimeError):
    """stop_reason == max_tokens: the reply was cut off, so it is not an answer."""


class Refused(RuntimeError):
    """stop_reason == refusal: the model declined to continue."""


class UnhandledStop(RuntimeError):
    """A stop_reason this code has never seen. Fail safely and keep the trace."""


def run_agent(goal: str, max_steps: int | None = None, verbose: bool = True) -> str:
    cfg = Config()
    client = GatewayClient(cfg)
    tracer = Tracer("lab1")
    budget = BudgetGuard(cfg.budget_usd, cfg.model)
    limit = max_steps or cfg.max_steps

    messages = [{"role": "user", "content": goal}]
    tracer.emit("start", goal=goal, model=cfg.model, max_steps=limit)

    for step in range(1, limit + 1):
        budget.check()

        response = client.messages(messages, tools=lab_tools.SCHEMAS, system=SYSTEM)

        stop = response.get("stop_reason")
        blocks = response.get("content", [])
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        cost = budget.record(response.get("usage", {}))
        tracer.step(step, stop or "none", text=text, tools=[c.get("name") for c in calls])
        if verbose:
            print(f"  step {step}: stop={stop} tools={[c.get('name') for c in calls] or '-'} "
                  f"(${cost:.4f}, {budget.summary()})")

        # One branch per stop_reason. "Not tool_use" is not a synonym for "done":
        # that is how a truncated or refused reply gets reported as an answer.
        if stop in ("end_turn", "stop_sequence"):
            if not text:
                raise UnhandledStop(f"model ended the turn with no text (stop_reason={stop})")
            tracer.emit("finish", answer=text[:400], spend=budget.summary())
            return text

        if stop == "max_tokens":
            tracer.emit("stopped", reason="max_tokens", step=step)
            raise Truncated(
                f"reply was cut off at the token limit after {step} steps; "
                f"raise max_tokens or ask for a shorter answer. {budget.summary()}")

        if stop == "refusal":
            tracer.emit("stopped", reason="refusal", step=step)
            raise Refused(f"the model declined to continue. {budget.summary()}")

        if stop == "pause_turn":
            # A long-running turn. Send the conversation back unchanged to resume.
            messages.append({"role": "assistant", "content": blocks})
            continue

        if stop != "tool_use":
            tracer.emit("stopped", reason=f"unhandled stop_reason={stop}", step=step)
            raise UnhandledStop(str(stop))

        # The assistant turn goes in BEFORE the results, and every tool_use block
        # gets a matching tool_result in ONE user message.
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for call in calls:
            out, ok = lab_tools.dispatch(call["name"], call.get("input", {}))
            tracer.tool(call["name"], call.get("input", {}), out, ok)
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": out, "is_error": not ok})
        messages.append({"role": "user", "content": results})

    # Falling out of the loop means the agent never finished. That is the step
    # limit doing its job — an agent without one is a production incident.
    tracer.emit("step_limit", limit=limit)
    raise StepLimitExceeded(
        f"agent did not finish within {limit} steps. {budget.summary()}"
    )


if __name__ == "__main__":
    from fixture_server import serve_in_background
    serve_in_background()
    goal = " ".join(sys.argv[1:]) or (
        "Fetch the ingest-tier status from http://127.0.0.1:8137/status.json, "
        "read limits.txt from the workspace, and tell me whether the service is "
        "over capacity. If it is, compute by what percentage the queue depth "
        "exceeds the limit, and name the escalation contact."
    )
    print("GOAL:", goal, "\n")
    try:
        print("\nANSWER:\n" + run_agent(goal))
    except (StepLimitExceeded, BudgetExceeded, Truncated, Refused, UnhandledStop) as exc:
        print(f"\nHALTED: {exc}")
        sys.exit(1)
