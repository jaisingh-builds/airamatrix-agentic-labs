# Lab 5.2 — Eval harness: a golden set and a gate that blocks

`run_evals.py` runs the Lab 5.1 **investigate** agent on every case in
`golden/cases.json`, grades each result with deterministic checks
(`graders.py`), and exits with a code a pipeline can gate on.

| Exit | Meaning |
|---|---|
| 0 | gate passed: pass rate ≥ `--min-pass` (0.85) **and** no critical check failed in any run |
| 1 | gate failed |
| 2 | could not run (no gateway config, every run errored) |

* **Outcome** checks look at what it proposed (`action_in`, `key_equals`, `not_change`,
  `value_at_most`, `mentions`, `not_mentions`); **trajectory** checks look at how
  (`called`, `read_before_write`, `max_tool_calls`).
* A check marked `critical` fails the gate on its own — a safety property is never averaged away.
* Errors (no valid output) count as failures.
* Each eval run gets a private aira-ops with fresh data and a read-only token.

```bash
python3 -m unittest test_graders test_judge -v                 # offline, free
python3 run_evals.py                                           # every case once (≈ $0.8)
python3 run_evals.py --repeat 3 --cases backlog-cause          # consistency
python3 run_evals.py --regrade fixtures/live-runs.json         # re-grade saved outputs: no model, $0
python3 judge.py calibrate --mode both                         # LLM judge vs human labels
```

## What happened on 25 Sep 2026

| Step | Change | Result | Cost |
|---|---|---|---|
| 1 | prompt v1, 6 cases | 6/6 PASS — but two proposals were a full revert to 16 | $0.69 |
| 2 | golden set tightened from the reviewer's blocks (`value_at_most` 8, critical) | `--regrade`: 4/6, FAIL | $0 |
| 3 | prompt v2 (don't undo a deliberate change without a record) | 6/6 PASS | $0.64 |
| 4 | v2, 3 cases × 3 | 4/9 — 3 schema errors, 18–19 tool calls | $0.85 |
| 5 | v3 (brevity, schema descriptions), new case `unverified-claim` | 7/12 — `unverified-claim` 0/3, one false alarm on the word "token" | $1.37 |
| 6 | grader fixed (the word is not a leak) | `--regrade`: 9/12 | $0 |
| 7 | v4 (if the request is the only source, ask on the record) | **12/14, 86% — PASS**, no critical failures; 1 schema error, 1 missed `get_ticket` | $1.53 |

Step 1 passed and was wrong; step 3 passed once and was flaky. Repeats and a
golden set built from real failures told the truth.

## The judge (`judge.py`)

10 real outputs with human labels (`judge_calibration.json`; one is an edited copy
of a real output and says so). The number that matters is **false passes**.

| Rubric | Mode | Agreement | False passes |
|---|---|---|---|
| v1 | blind | 90% | 1 — `run2-claim-in-prompt` |
| v1 | with reference facts | 90% | 1 — same item |
| v2 ("claims in the question are not evidence") | both | 100% | 0 |

v2 was tuned on these items, so it is not proven until it agrees on items it hasn't seen.
Deterministic checks gate the merge; the judge is for the fuzzy part, once calibrated.

## Your tasks (starter/graders.py)

1. `read_before_write` — did it read the value it wants to change?
2. `gate()` — pass rate **and** no critical failure.

```bash
LAB52_TARGET=starter python3 -m unittest test_graders    # fails until you finish
```
