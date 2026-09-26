# Day 4 — Orchestration, evals, observability and agentic CI/CD

Day 4 builds on Day 3's **aira-ops** and its MCP server. The agents here get a
read-only token and a read-only MCP server; the one write in the day is made by
plain code, after a human approves it.

**Day 4 is the one day with dependencies** (the rest of the repo is stdlib-only):

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: bootstrap\day4.ps1 setup
pip install -r day4-orchestration-evals-cicd/requirements.txt   # claude-agent-sdk, langgraph (pinned)
make day4-test                                            # every offline suite - no model, no cost
```

Node 22.6+ is needed for the MCP server (it runs `server.ts` directly). The live
labs use your gateway key from `.env`, exactly as on Days 1–3.

**On Java, or stuck on the Python/Node setup?** The same three labs exist in Java / Spring Boot,
with the same controls and gate rules and no Agent SDK, CLI or Node: see [`java/`](java/README.md).

| Lab | What | Live cost |
|---|---|---|
| [5.1 Multi-agent handoff](lab5-1-handoff/README.md) | Investigate → review → human gate → apply. Shared SQLite state, checkpoint/resume, idempotent write. Same flow in LangGraph. | ≈ $0.25 a run |
| [5.2 Eval harness](lab5-2-evals/README.md) | Golden set from real tickets, outcome + trajectory graders, pass-rate gate, repeats, LLM judge calibration | ≈ $0.70 for the set |
| [5.3 PR review stage](lab5-3-pr-review/README.md) | Headless Claude Code reviews a diff; secrets and hallucinated findings handled in code; exit code gates the merge | ≈ $0.15–0.30 a PR |
| [Capstone](capstone/README.md) | Teams of 3–4: extend the pipeline or build one agent. Guardrail + eval + trace + human approval. | team budget |
| [**AgentCore reference system**](agentcore/README.md) | Everything above, in production form on Amazon Bedrock AgentCore: three agents on Runtime, Gateway (MCP) + Cedar Policy, Identity per agent, Memory, Knowledge Base, Guardrail, online + batch Evaluations, one dashboard. Ten steps, each with its own README. | a few $ per stack per day (AWS) |

Shared pieces in [`common/`](common): `spans.py` (JSONL tracing, redaction at
the sink) and `trace_view.py` (print a trace as a tree).

CI: [`.github/workflows/day4.yml`](../.github/workflows/day4.yml) runs offline
tests, the eval gate, and the PR review, each with the least permission it needs.

## Checkpoint

Capstone demo delivered and scored; each team hands over working code with its
guardrail, eval and trace in place.
