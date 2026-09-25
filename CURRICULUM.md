# Curriculum — Agentic AI & Agentic Coding

AiraMatrix · Thane, Mumbai · Trainer: Jai Singh

Four days, one lab track per day. Days 3 and 4 land in this repo before those
sessions start — `git pull` on the morning of Day 3.

| Day | Date | Theme |
|---|---|---|
| 1 | 18 Sep 2026 | Agentic AI Foundations |
| 2 | 19 Sep 2026 | Agentic Coding with Claude Code |
| 3 | 25 Sep 2026 | MCP, agent integration, security, multi-agent, evals, CI/CD |
| 4 | 26 Sep 2026 | Orchestration, evals, observability, agentic CI/CD and the capstone |

---

## Day 1 — Agentic AI Foundations

Get to a working agent quickly: enough concept to design one properly, then
straight into building a tool-using agent from first principles.

| Lab | What | Time | Cost |
|---|---|---|---|
| [1.1](day1-foundations/lab1-bare-metal-loop/README.md) | Build an agent loop from scratch, three tools, full tracing | 60 min | ~$0.02 |
| [1.2](day1-foundations/lab2-tool-schema-clinic/README.md) | Tool schema clinic — measure what a description is worth | 30 min | ~$0.12 |
| [1.3](day1-foundations/lab3-failure-gallery/README.md) | Five classic agent failures, reproduced and fixed | 30 min | free |

Checkpoint: [day1-foundations/CHECKPOINT.md](day1-foundations/CHECKPOINT.md) —
a working, traced agent loop with at least two tools and a hard step limit.

## Day 2 — Agentic Coding with Claude Code

Drive real greenfield and brownfield work through Claude Code on your own
stack, with a disciplined review loop and honest measurement.

| Lab | What | Time |
|---|---|---|
| [Lab 2](day2-agentic-coding/greenfield/SPEC.md) | Greenfield: spec to running service, agent-driven | 60 min |
| [Lab 3](day2-agentic-coding/brownfield/README.md) | Brownfield: comprehension → characterisation tests → change | 75 min |
| [Lab 3b](day2-agentic-coding/grounding-clinic/README.md) | Write your first real CLAUDE.md, commands and a hook | 30 min |

Checkpoint: [day2-agentic-coding/CHECKPOINT.md](day2-agentic-coding/CHECKPOINT.md)
— one reviewed, merged agent-assisted change with a written review note.

## Day 3 — MCP, integration, security, multi-agent, evals, CI/CD

Everything runs against **aira-ops**, a small internal API. See
[day3-integration-security/README.md](day3-integration-security/README.md).

| Lab | What |
|---|---|
| 4.1 | Tool design clinic — rewrite three bad tools, measure calls, errors, correctness |
| 4.2 | TypeScript MCP server over aira-ops, used from Claude Code and a Python client, approval gate on writes |
| 4.3 | An agent inside a service: streaming, cancel, timeouts, budget, persistence |
| 4.4 | Red team: an agent reading content it must not trust |

## Day 4 — Orchestration, evals, observability, agentic CI/CD and the capstone

See [day4-orchestration-evals-cicd/README.md](day4-orchestration-evals-cicd/README.md).
Day 4 is the one day with dependencies: `pip install -r day4-orchestration-evals-cicd/requirements.txt`
(claude-agent-sdk, langgraph).

| Lab | What |
|---|---|
| 5.1 | Multi-agent handoff: two-stage pipeline, shared state store, human approval gate |
| 5.2 | Eval harness: golden set from real runs, CI job that blocks on failure |
| 5.3 | Agent-assisted PR review as a pipeline stage |
| Capstone | Teams of 3–4: a guardrail, an eval, a trace and a human approval point; 10-min demo, 100-point rubric |

---

For setup, budget, and repo layout, see [README.md](README.md). For lab
authoring conventions, see [CLAUDE.md](CLAUDE.md).
