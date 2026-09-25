# Capstone rubric (100 points)

Score each criterion on the anchors; half-way scores are fine. A missing mandatory
element (guardrail, eval, trace, human approval point) caps its criterion at the
lowest band.

| Criterion | Points | Full marks | Half | Low |
|---|---|---|---|---|
| **Working functionality** | 25 | Live run end to end on real data; handles a failure path on stage (timeout, bad input, reject) | Works on the happy path only; one manual fix during the demo | Mostly slides or a recording; crashes |
| **Agent design and pattern fit** | 20 | Pattern chosen for a reason the team can state (why not a single agent / why a reviewer); structured hand-offs; state persisted | Reasonable pattern, weakly justified; free-text hand-offs | Multi-agent for its own sake, or no clear design |
| **Tool and MCP integration** | 15 | Few, well-described tools with typed inputs; MCP or SDK used correctly; read vs write separated | Tools work but are broad or loosely typed | Agent shells out / has more access than it needs |
| **Guardrails and security** | 15 | Guardrail in code shown refusing live; least-privilege token; secrets out of code, logs and traces; untrusted input treated as data | Guardrail present but only described, or one leak (e.g. token in a trace) | Prompt-only safety; secrets in the repo |
| **Testing and observability** | 10 | ≥ 5 golden cases from real work, outcome + trajectory checks, repeat runs; a trace used to explain a failure | Eval exists but tiny or outcome-only; trace shown but not used | No eval number; no trace |
| **Demo and documentation** | 15 | 10 minutes on time; clear story; `TEAM.md` complete; another team could run it from the README | Over time or hard to follow; README partial | No README; can't be run by others |

**Checkpoint:** capstone demo delivered and scored; each team hands over working
code with its guardrail, eval and trace in place.
