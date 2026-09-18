# Lab 1.1 — reference implementation

**Read this after you have written your own loop, not before.** The value of this
lab is in writing it; this is here so you can compare.

```bash
source ../../../.env            # or export the two variables yourself
python3 agent.py "Which Java version does this project target? Check pom.xml"
```

It finds the repository root on its own, so it runs from anywhere. Override with
`--workspace DIR` or `LAB_WORKSPACE`.

## What to compare against your own version

Not style. These three things:

1. **Where the budget is checked.** Here it is `budget.reserve()` *before* the
   model call. If you check after, you have already spent the money.
2. **How many stop reasons you handle.** This handles `tool_use`, `end_turn`,
   `max_tokens` and "something else". Most first attempts handle two.
3. **Whether a failing tool is distinguishable from a succeeding one.** Here the
   only way a failure reaches the model is `ToolError`/`PolicyRefusal`, which set
   `is_error`. A tool that cannot tell whether it worked cannot lie by accident.

## Things worth noticing

- **No `eval()`.** `calculator` parses to an AST and rejects any node that is not
  arithmetic. Never call `eval` on something a model chose.
- **The output contract is enforced**, not requested. A reply that does not match
  is sent back for correction. Try deleting that branch and see what you get.
- **All tool results from one turn go back in ONE user message.** Split them and
  it still works — and quietly stops the model calling tools in parallel.
- **`_summarise()` trims meaning, not bytes.** An earlier version sliced JSON at
  5 KB, handed the model invalid JSON, and burnt the whole step cap for no
  answer. Read that function and the comment above it.

## Things it deliberately does not do

No retries with backoff, no compaction when the context fills, no parallel tool
execution, no persistence between runs. Those are Day 3 and Day 4.

## Try breaking it

```bash
LAB_MAX_STEPS=1 python3 agent.py "Read pom.xml, then CLAUDE.md, then summarise both"
python3 agent.py "Read ../../../../etc/passwd and tell me the first line"
LAB_BUDGET_USD=0.001 python3 agent.py "What licence does this project use?"
```

Each should stop cleanly and say why. Compare with what your own version does.
