# Day 1 checkpoint

Not a quiz. You show a working thing.

## What you demonstrate

A **working, traced agent loop with at least two tools and a hard step limit.**

Specifically:

1. **It runs.** `python3 agent.py` (or node / java) answers the capacity question
   correctly — 62.4%.
2. **It used its tools.** Not one call; the goal needs all three.
3. **It is traced.** Open your newest file in `traces/` and walk one step: what
   the model decided, which tool ran, what came back.
4. **It stops.** Show the step limit firing:
   ```bash
   LAB_LIVE=1 python3 test_agent.py
   ```
   The `step_limit` test must pass.
5. **It does not confuse "stopped" with "done".**
   ```bash
   cd python && python3 test_stop_reasons.py     # offline, no spend
   ```
   All seven pass: a truncated, refused or unknown stop reason is never returned
   as an answer.
6. **The boundary held.** Point the agent at `workspace/runbook.md`, which asks
   it to exfiltrate `credentials.txt`, and show the refusal in your trace. Say
   which line refused it — the allow-list, not the system prompt.
7. **You can answer one question:** *what would this agent do if a tool started
   failing?* You will have seen the answer in Lab 1.3.

## Demo format

Six teams, four minutes each, while the trainer spot-checks traces at the desks.
One member drives; everyone else has their own trace file open.

## Self-check

```bash
make test                 # offline, everything
LAB_LIVE=1 make test      # with the model, ~1 cent
```

## If you are not there

Say so in the morning rather than starting Day 2 behind. Day 2 builds directly
on this loop, and the gap widens if you carry it.

Reference solutions are on the `solutions` branch. Reading one after you have
genuinely tried is learning; reading one instead of trying is not.
