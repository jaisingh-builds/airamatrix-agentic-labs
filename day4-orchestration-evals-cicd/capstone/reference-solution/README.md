# Capstone reference solution — the SLA-breach responder

A worked answer to the [capstone](../README.md): one agent done properly, with all four mandatory items in code,
run live, locally and on Amazon Bedrock AgentCore. Read it **after** your own demo — it is one good answer, not
the only one.

| Folder | What | Status |
|---|---|---|
| [`SPEC.md`](SPEC.md) | the contract: tools, guardrail rules and messages, gate, statuses, trace spans, CLI, eval, AgentCore mode | shared by all three |
| [`golden/cases.json`](golden/cases.json) | 7 golden cases from real aira-ops data, outcome + trajectory checks, frozen gate | shared |
| [`fixtures/`](fixtures/) | a proposal the guardrail must refuse (for the $0 refusal demo) | shared |
| [`java/`](java/README.md) | **reference implementation**: local mode + AgentCore container (Spring Boot, AWS SDK v2, MCP Java SDK) | verified live 26 Sep 2026 |
| [`python/`](python/) | the same SPEC in Python (standard library + labkit) | see its README |
| [`node/`](node/) | the same SPEC in Node | see its README |

Aira-ops data comes from the Day 3 seed (`day3-integration-security/aira-ops/seed.json`), unchanged.

## Which option, and why

The capstone offers "extend the morning pipeline" or "a single agent for a real AiraMatrix task". This answer is a
**single agent**, because the rubric's hardest question is *"why this pattern — why not a single agent, why a
reviewer?"*, and the honest answer for most real tasks is: one agent, plus code that checks it. Lab 5.1 already
showed a reviewer agent; this shows when you do not need one.

## The problem

The customer-success duty manager at AiraMatrix. Gold tenants have a 4-hour contract SLA. When slide ingest stalls,
someone must find which of a tenant's tickets and slide-analysis jobs are at risk of breaching (or have breached),
understand why, and post a customer update **before** the breach — today by hand, 20-30 minutes per incident, across
tickets, jobs and contract tiers. A wrong post (another tenant's name, an internal setting, a secret, a promise
nobody made) is a data-leak incident, and a comment cannot be unsent.

## The design

```
 request ─▶ agent (tool loop, read-only) ─▶ code guardrail ─▶ [ human gate ] ─▶ apply (plain code)
            sla_report · get_ticket ·        recompute SLA      who · why ·        own process, own
            get_config · submit_proposal     from source,        proposal hash      write token,
                                             check every claim                      idempotent op id
                 └─────────────── store (SQLite): runs · proposals · approvals · operations ───────────────┘
```

* **Why a single agent:** the language work (read ticket text, explain the cause, write a customer-appropriate
  update) is one job. What a second, reviewer agent would check here — elapsed minutes, which items are exposed,
  whose ticket it is, whether a comment names an internal setting — code checks exactly, for free, every run, and
  cannot be talked round.
* **Why the numbers are code, not model:** `sla_report` computes them at a fixed clock (`as_of`); the model copies
  them; the guardrail recomputes them from source and refuses any mismatch. The frozen clock also makes every run
  and every eval reproducible on data from 24 Sep.
* **Why a human:** the only action is customer-visible and irreversible. The agent proposes; a named person with a
  reason decides; plain code with its own credential writes.
* **Structured hand-offs, persisted:** agent → guardrail → human → apply all pass JSON through SQLite rows, never free text.

## The four mandatory items — where they are (Java)

| Mandatory | What counts | Where |
|---|---|---|
| **Guardrail in code** | every claim re-checked against the SLA recomputed from source; a customer comment may not carry secrets, internal settings, other tenants, foreign ids or links; a blocked proposal **cannot** be approved | [`Guardrails.java:129`](java/core/src/main/java/com/airamatrix/capstone/Guardrails.java#L129), [`Guardrails.java:175`](java/core/src/main/java/com/airamatrix/capstone/Guardrails.java#L175); refusal: `replay fixtures/blocked-leak.json` |
| also | least privilege per process: the agent refuses to start with a write/admin token in its environment; turn limit and budget cap before every call | [`ResponderAgent.java:75`](java/core/src/main/java/com/airamatrix/capstone/ResponderAgent.java#L75), [`ResponderAgent.java:92`](java/core/src/main/java/com/airamatrix/capstone/ResponderAgent.java#L92) |
| also | tenant boundary in code (the shared Gateway's credential can read every account) | [`Tools.java:103`](java/core/src/main/java/com/airamatrix/capstone/Tools.java#L103) |
| also (AgentCore) | Bedrock Guardrail on every model call; Cedar at the Gateway: the agent identity sees 0 write tools and a write is DENIED | [`BedrockConverseModel.java:58`](java/runtime/src/main/java/com/airamatrix/capstone/runtime/BedrockConverseModel.java#L58), [`GateCheck.java:56`](java/aws-tools/src/main/java/com/airamatrix/capstone/aws/GateCheck.java#L56) |
| **Eval** | 7 golden cases, deterministic outcome + trajectory checks, repeats, a frozen gate, a number | [`golden/cases.json`](golden/cases.json), [`Checks.java:134`](java/core/src/main/java/com/airamatrix/capstone/Checks.java#L134), results in [`java/results/`](java/results/) |
| **Trace** | common/spans.py JSONL, walkable with `trace_view.py`, incl. failed runs | [`Responder.java:49`](java/core/src/main/java/com/airamatrix/capstone/Responder.java#L49); samples in [`java/samples/`](java/samples/) |
| **Human approval point** | name + reason + time + proposal hash (+ AWS identity in AgentCore mode); refuses no reason, one-word reasons, agent identities, blocked proposals, second decisions; `apply` checks the decision record, not the status | [`Gate.java:58`](java/core/src/main/java/com/airamatrix/capstone/Gate.java#L58), [`Gate.java:101`](java/core/src/main/java/com/airamatrix/capstone/Gate.java#L101) |

## Rubric → evidence

| Criterion | Evidence in this solution |
|---|---|
| Working functionality (25) | live local run end to end incl. approval and one idempotent write; failure paths on stage: forbidden token (refuses before cost), apply without approval, agent-identity approver, blocked proposal, stale ticket, Bedrock Guardrail intervention, shared-data apply refused |
| Agent design and pattern fit (20) | single agent + deterministic verifier + human gate + plain-code apply, with the "why not a reviewer agent" answer above; JSON hand-offs; SQLite state |
| Tool and MCP integration (15) | 3 typed, bounded read tools + the contract tool; reads separated from the one write; AgentCore reads through the Gateway over the MCP Java SDK with an Identity token Cedar filters to read-only |
| Guardrails and security (15) | code guardrail shown refusing live and offline; scoped tokens per role (read vs apply); no secrets in code, traces or results (redaction at the sink; the reason and comment text never enter a trace) |
| Testing and observability (10) | 7 golden cases x 2, first-attempt vs final pass reported; traces used to find and fix three real faults (see [`java/README.md`](java/README.md#what-the-live-runs-taught-us-26-sep-2026)); OTel GenAI spans in CloudWatch |
| Demo and documentation (15) | the 10-minute script below; [`TEAM.md`](TEAM.md) filled; every command in the READMEs was run |

## Results (26 Sep 2026)

| Run | Result | Cost |
|---|---|---|
| local, one run ACC-1001 + approve + apply | awaiting_approval → approved → applied (HTTP 201, re-apply a no-op) | $0.03 |
| local eval, 7 cases x 2, four iterations driven by traces ([`java/results/`](java/results/README.md)) | 14/14 every time; first attempt 12 → 11 → 12 → **13**/14 | $2.47 |
| local eval, 7 cases x 2 (final code) | **14/14 PASS**, first attempt 13/14, 1 retried, 0 unrecovered errors | $0.55 |
| AgentCore invoke ACC-1001 | awaiting_approval, guardrail PASS, 37 s | $0.04 |
| AgentCore eval, 7 cases x 1 (image v3) | 6/7, **gate FAIL**: the shared Bedrock Guardrail blocks one case's request as a prompt attack (LOW confidence, a false positive) — the gate fails closed, as it should | $0.19 |
| AgentCore eval, 7 cases x 1 (image v5, injection case declares `accept_refusal`) | **7/7 PASS**, 1 accepted as a guardrail refusal (the same false positive, still reported); first attempt 4/7 — 3 invocations right after the redeploy hung to the 10-min client timeout and passed on retry | $0.20 |
| AgentCore gate-check | agent identity: 7 tools, 0 write tools, write DENIED by Cedar | $0 |

## The 10-minute demo (local mode; commands from the repo root)

Before the demo: build (`java/README.md` step 0), start your aira-ops on 8177, have shell 1 (read token) and shell 2
(apply token) open. `CAP` is `java -jar day4-orchestration-evals-cicd/capstone/reference-solution/java/core/target/capstone-cli.jar`.

| Min | Segment | Command | What they see |
|---|---|---|---|
| 0-1 | Problem | — | the duty manager, the 4-hour SLA, a comment cannot be unsent |
| 1-3 | Design | the diagram above | why one agent + a code verifier, not a reviewer agent |
| 3-4 | Live run | shell 1: `$CAP run --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 --question "Duty manager: check SLA exposure for Sahyadri Pathology Labs before the 11:00 call."` | the SLA table (J-5501 breached 275/240, T-1001 at_risk 230/240), the proposal, `[guardrail] PASS - every rule` |
| 4-5 | Human approval | `$CAP apply RUN` in shell 2 → `refused: ... has no approval on record`; `$CAP approve RUN --by sla-responder --reason "..."` → refused (agent identity); `$CAP approve RUN --by "Your Name" --reason "..."`; shell 2: `$CAP apply RUN` twice | the decision row with who/why; HTTP 201, then the same op id: one comment |
| 5-6 | Guardrail refusing | `$CAP replay day4-orchestration-evals-cicd/capstone/reference-solution/fixtures/blocked-leak.json` then `$CAP approve RUN --by "Your Name" --reason "customer is waiting"` | `[guardrail] BLOCKED` with 4 rules; approving it: `the guardrail blocked this proposal (...). There is no override` |
| 6-8 | Eval + trace | open `java/results/<latest>.md`; `$CAP trace RUN`; `python3 day4-orchestration-evals-cicd/common/trace_view.py day4-orchestration-evals-cicd/capstone/reference-solution/java/samples/agentcore-contract-float-bug.jsonl` | 14/14 and the first-attempt number; a failed run's trace and the bug it revealed |
| 8-9 | AgentCore (optional) | `capstone-aws.jar invoke ...`, `gate-check` | the same run on AgentCore; Cedar DENIED for the agent identity |
| 9-10 | Next | — | the list below |

## What we would do next

1. Tune the shared guardrail (PROMPT_ATTACK at MEDIUM, or `guardContent` to scan only untrusted text) and re-run the
   AgentCore eval; it must pass before this runtime serves anyone.
2. A tenant-name deny list at apply time (today the guardrail refuses other tenants' **ids**; the eval catches names).
3. The approver identity from SSO instead of a typed name (AgentCore mode already records the AWS principal).
4. AgentCore batch evaluation on the runtime's spans (`agentcore/08-evaluations`), and the eval as a CI gate.
5. Memory of past updates per ticket, so the agent does not post the same update twice in one incident.
