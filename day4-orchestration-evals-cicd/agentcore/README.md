# AgentCore reference system — the whole of Day 4, on real AWS

Labs 5.1–5.3 teach each idea in isolation, offline, in a few hundred lines you can read.
This folder puts **all of them together in production form** on Amazon Bedrock AgentCore:
one multi-agent system that triages a real ticket in the aira-ops API you used on Day 3.

> Ticket **T-1001**: "Slide ingest backlog since 06:00". A supervisor agent sends an investigator to find the
> cause, gets an independent reviewer to check the proposed fix, and asks a **human** to approve it.
> The fix itself — raising a production config value — can only be applied by the human, and the
> platform (not the prompt) enforces that.

## What you will build

```
                        you / CI  ──InvokeAgentRuntime (SigV4 + runtimeUserId)──┐
                                                                                ▼
 ┌──────────────────────────── AgentCore Runtime (3 microVM-isolated agents, Strands) ───────────────────────────┐
 │  supervisor ──ask_investigator──► investigator          supervisor ──ask_reviewer──► reviewer                  │
 │     │  Memory (short + long term)      │ read-only            │                        │ read-only             │
 │     │  Guardrail on every model call   │                      │                        │                       │
 └─────┼──────────────────────────────────┼──────────────────────┼────────────────────────┼───────────────────────┘
       │ OAuth token from AgentCore Identity (per agent, per scope)                         │
       ▼                                  ▼                      ▼                        ▼
 ┌──────────────────────── AgentCore Gateway (one MCP endpoint) + Policy (Cedar, ENFORCE) ───────────────────────┐
 │  tools/list is FILTERED per caller      every tools/call is AUTHORIZED against the policies before it runs     │
 │  ops-read   (OpenAPI → aira-ops GET)    ops-write (comment, set_ingest_concurrency)   handbook (Lambda → KB)   │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                    ▲
              human approver ──approve.py (own identity, aira-ops/config scope)─┘

 Observability: OpenTelemetry → CloudWatch (traces, GenAI Observability)   Evaluations: online + batch w/ ground truth
 Dashboard: runtime · gateway · policy · guardrail · identity · memory · tokens · evaluation scores
```

| Day 4 idea (lab) | Here, on AgentCore | Step |
|---|---|---|
| Traces are the ground truth for agent behaviour | Transaction Search, OpenTelemetry from every runtime | 01, 06 |
| Guardrails outside the prompt | Bedrock Guardrail on every model call: prompt attack, credentials, PII | 02 |
| Least privilege per agent ("roles") | Cognito client + scope per agent, AgentCore Identity token vault, one IAM role per runtime | 03, 06 |
| Tools as a governed interface (MCP) | AgentCore Gateway: OpenAPI + Lambda targets behind one MCP endpoint | 04 |
| Human approval for irreversible actions | AgentCore Policy (Cedar): only a human's scope may change config, and never above 16 | 04, 07 |
| Knowledge grounding (RAG) | Bedrock Knowledge Base (the Day 3 ops handbook) as a Gateway tool | 04 |
| Hand-offs and multi-agent orchestration (5.1) | Supervisor → investigator / reviewer via `InvokeAgentRuntime` | 06, 07 |
| State across turns and sessions | AgentCore Memory: events + semantic facts + session summaries | 05 |
| Evals as a gate (5.2) | Online evaluation (production) + batch evaluation with ground truth (pre-release) | 08 |
| Metrics you can act on | One CloudWatch dashboard across every layer | 09 |

## Before you start

**Who runs what.** The trainer deploys one complete stack (`AC_PREFIX=aira-d4`) and demonstrates it.
If you have been given AWS credentials for the training account, you can deploy your own with your
participant id as the prefix (`AC_PREFIX=p05`) — every name is prefixed and every script reads what it
created from `out/state.json`, so stacks never collide. Otherwise follow along with the trainer's stack:
the READMEs tell you what to look for at every step.

You need:

| | |
|---|---|
| AWS CLI v2 + credentials | `aws sts get-caller-identity` works; region `ap-south-1` |
| Bedrock model access | Claude Sonnet 5 (`global.anthropic.claude-sonnet-5`) — override with `AC_MODEL_ID` |
| Python 3.12 with `boto3>=1.43` | `pip install -r requirements.txt` (the agents' own deps are built by step 6) |
| From the trainer | `AIRA_OPS_URL`, a read token, a write token, and `KB_ID` (the Day 3 handbook knowledge base) |

**Nothing secret is in this folder.** Tokens come from your environment; every script writes what it
creates to `out/` (gitignored, files are `0600`).

## Run it

Every step is one command, run **from inside the step's folder**. `PYTHONPATH=..` lets the step import
`common.py`. On Windows PowerShell use `$env:PYTHONPATH=".."; python <script>.py`.

```bash
export AC_PREFIX=aira-d4            # participants: your id, e.g. p05
export AWS_REGION=ap-south-1
cd day4-orchestration-evals-cicd/agentcore
```

| Step | Command (in the step folder) | Time | What you get |
|---|---|---|---|
| [01 Observability](01-observability/) | `bash enable.sh` | 1 min | Transaction Search on (once per account) |
| [02 Guardrail](02-guardrail/) | `PYTHONPATH=.. python create_guardrail.py` | 1 min | guardrail + version, tested |
| [03 Identity](03-identity/) | `PYTHONPATH=.. python setup_identity.py` | 1 min | Cognito pool, 4 clients, 3 AgentCore OAuth providers |
| [04 Gateway + Policy](04-gateway-policy/) | `PYTHONPATH=.. python setup_gateway.py` | 3 min | MCP gateway, 3 targets, 4 Cedar policies, role tests |
| [05 Memory](05-memory/) | `PYTHONPATH=.. python create_memory.py` | 3 min | memory with 2 long-term strategies |
| [06 Agents](06-agents/) | `PYTHONPATH=.. python deploy_agents.py` | 5 min | 3 runtimes, READY |
| [06 Agents, Java](06-agents-java/) *(alternative)* | `java -jar java-tools/target/agentcore-tools.jar deploy --image …` | 5 min | the same 3 agents as a Java container; `invoke` / `approve` in Java too |
| [07 Run](07-run/) | `PYTHONPATH=.. python invoke.py supervisor "Triage ticket T-1001"` | 2 min | the full flow |
| [08 Evaluations](08-evaluations/) | `PYTHONPATH=.. python online_eval.py` then `batch_eval.py` | 6 min | live scores + a regression run |
| [09 Dashboard](09-dashboard/) | `PYTHONPATH=.. python create_dashboard.py` | 10 s | one dashboard URL |
| [10 Teardown](10-teardown/) | `PYTHONPATH=.. python teardown.py --yes` | 2 min | everything deleted |

**Cost** for one stack for one day is small: a few dollars of model tokens (each full triage ≈ 3 agents ×
6–10 model calls), Runtime is billed per second of active CPU, Gateway/Memory/Evaluations per request.
The biggest line item is the model. **Run step 10 when you are done.**

## Things that will bite you (all hit while building this)

| Symptom | Cause and fix |
|---|---|
| `AgentCore Transaction Search` stuck / policy rejected | zsh turns `$ACC:l…` into garbage — `enable.sh` uses `${ACC}`; the SourceArn condition is not accepted, SourceAccount is |
| A Cedar policy is `CREATE_FAILED`, "overly restrictive" | a `forbid` with an unscoped `principal` — scope it: `principal is AgentCore::OAuthUser` |
| Gateway returns `request body is not valid JSON` | an OpenAPI **property** named `body` is taken as the whole request body — name it anything else (`comment`) |
| Agent: `Workload access token has not been set` | with SigV4 inbound auth, pass `runtimeUserId` on `InvokeAgentRuntime` — that is what lets the runtime mint a token for Identity |
| The supervisor posts **two** identical comments | boto3's default 60 s read timeout + automatic retry re-ran a 100 s agent. Set `read_timeout=900` and `total_max_attempts=1` for agent calls |
| Guardrail block crashes the supervisor (`DeleteEvent` denied) | after an intervention the session manager redacts the stored turn — the role needs `bedrock-agentcore:DeleteEvent` on the memory |
| Batch evaluation: `LogEventMissingException` | spans are still being indexed; wait 2–3 minutes, then `batch_eval.py --rescore` |
| Online evaluation config rejected: `serviceNames … length ≤ 1` | one config watches one agent — create one per agent |
| `get_config` works for the agent but the approver is denied | by design: the approver's scope is `config` only; `tools/list` shows it one tool |

## How this maps to the rest of Day 4

- **Lab 5.1 (hand-offs)** — the hand-off contract there is `ask_investigator` / `ask_reviewer` here; the
  reviewer receives the proposal and evidence, *not* the investigator's reasoning, for the same reason.
- **Lab 5.2 (evals)** — `golden.json` is the same idea as the lab's cases file: grow it every time
  production surprises you. The CI gate in 5.3 can run `batch_eval.py` before promoting a new agent version.
- **Capstone** — option C is "extend this system": add a policy, a golden scenario, or a new specialist.
