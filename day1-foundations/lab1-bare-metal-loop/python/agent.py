#!/usr/bin/env python3
"""Lab 1.1 — Build an agent loop from scratch. No framework.

You are writing the loop. That is the whole exercise: everything in the rest of
the programme sits on top of the cycle you are about to implement.

    observe -> decide -> act -> observe

Run it:   python3 agent.py
Check it: python3 test_agent.py

Five TODOs. Work top to bottom. The reference solution is on the `solutions`
branch — try each TODO before you look.
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

        # ------------------------------------------------------------ TODO 1
        # Call the model. Pass the conversation so far, the tool SCHEMAS, and
        # the SYSTEM prompt.  Look at GatewayClient.messages() for the signature.
        #
        #   response = ...
        raise NotImplementedError("TODO 1: call the model")

        # ------------------------------------------------------------ TODO 2
        # Pull the three things you need out of the response:
        #   stop   - response["stop_reason"]
        #   blocks - response["content"]  (a list of content blocks)
        #   calls  - only the blocks whose "type" == "tool_use"
        # Then record the cost:  budget.record(response.get("usage", {}))

        # ------------------------------------------------------------ TODO 3
        # If stop != "tool_use" the agent is done. Join the text from every
        # block whose "type" == "text" and return it.
        # Trace it first:  tracer.emit("finish", answer=..., spend=...)

        # ------------------------------------------------------------ TODO 4
        # Otherwise the model wants tools. Two rules that are easy to get wrong:
        #   a) Append the assistant's blocks to messages BEFORE the results.
        #   b) EVERY tool_use block needs a matching tool_result, and they all
        #      go back in ONE user message. Splitting them across messages
        #      quietly teaches the model to stop calling tools in parallel.
        #
        # For each call:  out, ok = lab_tools.dispatch(call["name"], call["input"])
        # Build:  {"type": "tool_result", "tool_use_id": call["id"],
        #          "content": out, "is_error": not ok}

        # ------------------------------------------------------------ TODO 5
        # Append the results as a single {"role": "user", ...} message and let
        # the loop go round again.

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
    except (StepLimitExceeded, BudgetExceeded) as exc:
        print(f"\nHALTED: {exc}")
        sys.exit(1)
