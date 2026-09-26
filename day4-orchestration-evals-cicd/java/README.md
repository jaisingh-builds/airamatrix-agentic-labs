# Day 4 in Java / Spring Boot

The same three labs as the Python track, with the same controls, contracts and gate rules:

| Lab | Module | Runnable jar |
|---|---|---|
| [5.1 Multi-agent handoff](lab51/README.md) | `lab51` | `lab51/target/lab51.jar` (CLI, plus a REST API with `serve`) |
| [5.2 Eval harness](lab52/README.md) | `lab52` | `lab52/target/lab52.jar` |
| [5.3 PR review stage](lab53/README.md) | `lab53` | `lab53/target/lab53.jar` |

## Why a Java track

The Python labs run their agents through `claude-agent-sdk`, which starts the Claude Code CLI,
which starts the aira-ops MCP server on Node - three runtimes to get right on every laptop.
Here an agent is a plain loop on the JVM:

| Python lab | Java track |
|---|---|
| `SdkRunner` (Agent SDK → Claude Code CLI) | `GatewayAgentRunner`: a tool-use loop against the gateway's Messages API |
| MCP server on Node, started read-only, 4 tools allow-listed | `AiraOpsTools`: the same 4 read tools as HTTP calls. There are no write tools at all |
| `output_format` JSON schema | a `submit_result` tool whose schema **is** the contract, validated, one fix-up round |
| `max_turns`, `max_budget_usd` | enforced before every model call; a failed stage still reports what it cost |
| refuses to start with a write token in the environment | same refusal, same message: agents and `apply` are separate commands |
| `common/spans.py` | `Spans`: the same JSONL records, so `python3 ../common/trace_view.py` reads Java traces too |
| `claude -p` for the PR review | one Messages API call with no tools |

The contracts are the Python lab's own JSON Schemas, exported unchanged, so a proposal that passes
in Java passes in Python.

## Setup (once)

You need **JDK 21** and **Maven 3.9** (`java -version`, `mvn -v`), the repo's `.env` with your
gateway key (as on Days 1-3), and aira-ops running (Day 3). Python is only needed to run aira-ops
itself.

From the **repo root**:

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab51,day4-orchestration-evals-cicd/java/lab52,day4-orchestration-evals-cicd/java/lab53 -am package -DskipTests
```

That builds `labkit`, `common` and the three lab jars. Offline tests (no model, no cost):

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/common,day4-orchestration-evals-cicd/java/lab51,day4-orchestration-evals-cicd/java/lab52,day4-orchestration-evals-cicd/java/lab53 -am test
```

Windows PowerShell: the same commands work; set variables with `$env:NAME="value"` instead of `export`.

## Environment variables (same names as the Python labs)

| Variable | Used by | What |
|---|---|---|
| `.env` → `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `LAB_MODEL` | all | the training gateway (read by labkit, exactly as on Days 1-2) |
| `AIRA_OPS_URL` | 5.1, 5.2 | default `http://127.0.0.1:8150` |
| `AIRA_OPS_READ_TOKEN` | 5.1 agents | a read-only caller token (`lab51 tokens`) |
| `AIRA_OPS_APPLY_TOKEN` | 5.1 `apply` only | the one write credential. Never in the shell that runs agents |
| `LAB_TRACE_DIR` | all | where traces go (default `traces/` at the repo root) |

**Gotcha:** an exported `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` beats `.env`. If calls fail
with 401 from the gateway, `unset` them and let `.env` decide.

## Layout

```
java/
  common/   GatewayAgentRunner, AiraOpsTools, Contracts (+ the exported schemas), Spans, ModelClient
  lab51/    pipeline: store (SQLite), stages, gate, apply, replay, tokens; REST API on :8170
  lab52/    graders, gate, live harness (fresh aira-ops per run), LLM judge + calibration
  lab53/    diff, secret masking, reviewer call, evidence check, exit codes
```

Each lab's README walks through it step by step. The exercises are the same as in Python: the
solution code is in place, and each exercise region is marked `// >>> TODO n` ... `// <<< TODO n`.
Delete what is between the markers and make the tests pass again.
