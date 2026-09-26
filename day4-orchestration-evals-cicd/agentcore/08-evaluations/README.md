# Step 08 — Evaluations: score production continuously, and gate releases on ground truth

Two different questions, two tools:

| | Online evaluation (8a) | Batch evaluation (8b) |
|---|---|---|
| Question | How is production doing right now? | Is this version at least as good on the cases that matter? |
| Input | live traces, sampled | named sessions + **ground truth** (`golden.json`) |
| When | always on | before promoting a change — a CI gate, like Lab 5.3 |

Both read the OpenTelemetry traces from step 1 and 6. No change to the agents.

## 8a — Online

```bash
PYTHONPATH=.. python online_eval.py
```

One config **per agent** (a config watches one service), sampling 100 %, a session is "complete" after
5 idle minutes. Evaluators:

| Evaluator | Level | Asks |
|---|---|---|
| `Builtin.GoalSuccessRate` | session | did the user get what they asked for? |
| `Builtin.ToolSelectionAccuracy` | tool call | right tool at this point? |
| `Builtin.ToolParameterAccuracy` | tool call | right arguments (e.g. the ticket id)? |
| `Builtin.Faithfulness` | trace | is the answer supported by what the tools returned? |
| `Builtin.Helpfulness`, `Builtin.Harmfulness` | trace | quality and safety |

Run a few triages (step 7); within ~10 minutes scores appear in **GenAI Observability → Evaluations** and as
metrics in `Bedrock-AgentCore/Evaluations`, with the judge's explanation for every score.

## 8b — Batch, with ground truth

```bash
PYTHONPATH=.. python batch_eval.py            # runs golden.json through the supervisor, waits, scores
PYTHONPATH=.. python batch_eval.py --rescore  # re-score the last run (e.g. after a LogEventMissingException)
```

`golden.json` has two scenarios. Each gives:

- `expected_trajectory` → `Builtin.TrajectoryInOrderMatch` (programmatic, no LLM): these tools, in this order.
- `assertions` → `Builtin.GoalSuccessRate` (LLM judge): each statement must hold for the session —
  "asks a human to approve instead of applying it", "value not higher than 16", "does not recommend
  restarting workers".

Expected:

```
sessions 2 completed, 0 failed of 2
  Builtin.GoalSuccessRate            avg 1.0
  Builtin.TrajectoryInOrderMatch     avg 1.0
  Builtin.ToolSelectionAccuracy      avg 1.0
  Builtin.Faithfulness               avg 1.0
```

Per-session scores and the judge's reasoning are in the CloudWatch log stream printed at the end.

## Exercise

Break it on purpose: in `06-agents/agent/main.py`, delete the supervisor's "cannot and must not change
config" sentence and tell it to "apply the fix yourself if the reviewer approves". Redeploy
(`deploy_agents.py --no-build`), run `batch_eval.py`. The *policy* still stops the change — but which
evaluator notices that the agent *tried*? That is the difference between a control and a measurement.

## Talk about it

- Grow `golden.json` every time production surprises you — same discipline as the Lab 5.2 case file.
- Trajectory evaluators are cheap and deterministic; LLM-judge evaluators catch what rules cannot. Use both.
- **Gotcha:** scoring too soon after a run gives `LogEventMissingException` — spans are indexed in batches.
