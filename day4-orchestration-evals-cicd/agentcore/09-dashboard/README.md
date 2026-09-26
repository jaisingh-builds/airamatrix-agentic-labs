# Step 09 — Dashboard: one screen for every layer

```bash
PYTHONPATH=.. python create_dashboard.py      # prints the dashboard URL
```

| Row | Widgets | Namespace |
|---|---|---|
| Runtime | invocations, p90 latency, errors — per agent (the Java agents too, as `java …`, when deployed) | `AWS/Bedrock-AgentCore` |
| Gateway + Policy | tool calls by tool · allow vs deny · denials by determining policy | `AWS/Bedrock-AgentCore` |
| Safety + identity + cost | guardrail evaluated vs intervened · OAuth tokens issued per agent · Claude tokens in/out | `AWS/Bedrock/Guardrails`, `AWS/Bedrock-AgentCore`, `AWS/Bedrock` |
| Quality + memory | evaluation scores 0–1 (online and batch) · memory records extracted | `Bedrock-AgentCore/Evaluations`, `AWS/Bedrock-AgentCore` |

All of these are emitted by AgentCore itself — this step only arranges them. The script uses exact
dimensions where it knows the resource (runtime ARNs, gateway, policy engine, guardrail) and `SEARCH()`
expressions for per-tool and per-evaluator breakdowns, so new tools and evaluators appear automatically.

## Read it like an operator

- **Deny decisions rising, invocations flat** → an agent keeps trying something it is not allowed to do:
  a prompt regression, or someone probing. The "denials by policy" bar says which rule.
- **Guardrail interventions spike** → attack traffic, or a new legitimate use that the guardrail blocks.
- **GoalSuccessRate drops after a deploy** → roll back; then add the failing session to `golden.json`.
- **Tokens up, sessions flat** → loops or bloated context; open a trace in GenAI Observability.

Alarms are one call away (`put_metric_alarm` on any of these) — try one on `DenyDecisions`.
