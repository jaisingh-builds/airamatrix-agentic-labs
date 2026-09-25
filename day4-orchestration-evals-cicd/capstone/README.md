# Capstone

Teams of 3–4. About 2 hours to build, then a **10-minute demo + 5 minutes Q&A**.

Pick one:

* **Extend the morning pipeline** (Lab 5.1) — a third stage, a new action with
  its own guardrail, a second account, a LangGraph version with a real
  checkpointer, a CI gate on your own golden cases…
* **Build a single agent** for a real AiraMatrix task you know — against
  aira-ops, a repo, or your own read-only data. One agent done properly beats three
  done loosely.

## Mandatory (no demo without all four)

| | What counts | What doesn't |
|---|---|---|
| **Guardrail** | A control in code that stops a bad action: allowlist, schema + validation, scoped token, budget cap, redaction, refusing a write | "The prompt tells it not to" |
| **Eval** | ≥ 5 golden cases from real work, deterministic checks, run with `run_evals.py` or your own harness, with a number you can show | "We tried it a few times" |
| **Trace** | A JSONL trace (use `common/spans.py`) you can open and walk through for one run — ideally a failed one | Console prints |
| **Human approval point** | A place where a person decides, with who/why recorded, and the system refuses to proceed without it | A `y/n` with nothing stored |

## Demo script (10 minutes)

1. The problem and who has it (1 min)
2. The design: pattern and why that pattern (2 min) — single agent / sequential / supervisor / evaluator
3. Live run, including the human approval point (3 min)
4. The guardrail stopping something — show it refusing (1 min)
5. Eval results and one trace (2 min)
6. What you'd do next (1 min)

Hand over: the branch, `TEAM.md` filled in, eval results, one trace file.

## Scoring (100)

See [`rubric.md`](rubric.md). Scorers fill [`scorecard.csv`](scorecard.csv).

## Budget and safety

* Every live call goes through the gateway on your key. Set `max_budget_usd` on
  every agent and `--budget` on evals. Aim to spend under $5 as a team.
* Writes go only to your own aira-ops (`--db` and `--port` of your own), never
  a shared one. Tokens in the environment, never in code or in `TEAM.md`.
