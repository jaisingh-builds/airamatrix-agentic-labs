# Team Reference (worked example)

**Members:** the trainers (a filled example of the hand-over form - yours will name your team)
**Branch:** `capstone-reference-java` (`day4-orchestration-evals-cicd/capstone/reference-solution/`)

## Problem
The customer-success duty manager has to spot tickets and slide-analysis jobs that are about to breach a tenant's
contract SLA (4 hours for gold) and post a customer update before they do. Today that is done by hand during every
ingest incident - reading tickets, jobs and contract tiers, 20-30 minutes each time, several times a week when ingest
is unstable - and a wrong post (another customer's name, an internal setting, a promise nobody made) is a data-leak
incident that cannot be unsent.

## Pattern
**Single agent + deterministic guardrail + human gate + plain-code apply.** One agent does the language work: reads
the tickets, explains the likely cause, drafts the customer update. We did *not* add a reviewer agent: everything a
reviewer would check here - elapsed minutes, which items are exposed, whose ticket it is, whether the comment names
an internal setting - code checks exactly, for free, and cannot be talked round. A human approves because the only
action is customer-visible and irreversible.

```
request ─▶ agent (read-only tools) ─▶ code guardrail ─▶ human gate (who, why, proposal hash) ─▶ apply (own token, idempotent)
                     └──────────── SQLite: runs · proposals · approvals · operations ────────────┘
```

## Tools and access
| Tool / MCP server | Read or write | Token / scope |
|---|---|---|
| `sla_report` (code over aira-ops `/accounts`, `/tickets`, `/jobs`) | read | `sla-responder` caller token: read-only, ACC-1001 only |
| `get_ticket` (aira-ops `/tickets/{id}`, body and comments bounded, labelled untrusted) | read | same |
| `get_config` (aira-ops `/config/{key}`, 4 allowlisted keys) | read | same |
| `submit_proposal` (the contract, validated) | none | - |
| `apply` - `POST /tickets/{id}/comments` with an Idempotency-Key | **write** | `capstone-apply` caller token: write, ACC-1001 only, only in shell 2, never in the agent's process |
| AgentCore mode: Gateway `ops-read___*` over MCP | read | the investigator's Identity provider, scope `aira-ops/read` - Cedar shows it 0 write tools |

## Guardrail
Stops: a proposal whose numbers or exposed items differ from the SLA **recomputed from source**, a customer update
on a ticket that is not at risk or not ours, and a comment carrying a secret, an internal setting name, another
tenant's id, a foreign ticket/job id or a link. Code: `java/core/.../Guardrails.java` (`verify`, `outbound`); the
outbound rules run again in `Gate.apply`. A blocked proposal cannot be approved - there is no override.
Shown refusing: `capstone-cli.jar replay fixtures/blocked-leak.json` (4 rules fire, including
`comment.internal_config: internal setting 'ingest.max_concurrent_jobs' in a customer update`), then approving it is refused.
Also: the agent refuses to start with `AIRA_OPS_TOKEN` in its environment (we hit this for real: our shell had it).

## Human approval point
`approve RUN --by NAME --reason WHY` (`Gate.decide`). Recorded in `approvals`: decision, name, principal (OS user
locally, the AWS identity in AgentCore mode), reason, time, and the SHA-256 of the exact proposal. Refused without a
name or reason, with a one-word reason, with an agent identity as approver, on a blocked proposal, or a second time.
`apply` refuses without an approval **record** (a status field alone opens nothing), if the proposal changed since it
was approved, for a non-local host, and for runs that read the shared aira-ops through AgentCore.

## Eval
Golden cases (7): `golden/cases.json` - T-1001 backlog (Lab 4.2 / 5.1), a quiet account (nothing due), the T-1007
prompt injection (seeded Day 3), a cross-tenant request from a CS chat, a breached job with no ticket of its own,
the T-1002 overlay breach, and an unverified claim in the request (Lab 5.1 run 1597824a97).
Checks: outcome (status, action, ticket, exposed items, comment content, guardrail passed) + trajectory (first call,
reads, no successful cross-tenant read, max tool calls); 9 are critical.
Result (local, final code): **14/14 passed over 7 cases x 2 runs, first attempt 13/14, cost $0.55** (`java/results/`).
Four iterations, each driven by a trace, took first-attempt from 12 to 13 of 14. AgentCore: 7/7 with one run
accepted as a guardrail refusal - the shared Bedrock Guardrail's false positive on the injection case, counted
separately so it stays visible (see What we'd do next).

## Trace
File: `java/samples/agentcore-contract-float-bug.jsonl`. The first AgentCore run failed with
`contract: no proposal matching the contract after 3 attempts`; the trace showed three `contract.rejected` spans, all
`$.exposed[0].elapsed_minutes: expected "integer", got float`, with the model sending all six keys. The model sent
`275`; our Converse translation turned it into `275.0`. Fixed in `DocJson`, pinned by a test, redeployed as `v2`:
the next run passed. Also walked: `local-approved-applied-with-refusals.jsonl` (run → three refusals → decision → one write → a refused second decision).

## What we'd do next
Tune the shared guardrail's prompt-attack strength (it blocks one golden case's innocent request at LOW confidence),
add a tenant-name deny list at apply time, take the approver from SSO, run AgentCore batch evaluation on the spans,
and make the eval a CI gate.
