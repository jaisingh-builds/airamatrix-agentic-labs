# Lab 5.1 in Java: multi-agent handoff with two stages, shared state and a human gate

This is the Java / Spring Boot port of [`../../lab5-1-handoff`](../../lab5-1-handoff/README.md). It works the
same way and teaches the same controls. You need only a JDK: no Claude Code CLI, no Node MCP server and
no `pip install`.

```
 question ─▶ investigate ─▶ review ─▶ [ human gate ] ─▶ apply (plain code)
             (agent,         (agent,     approve/reject     one write, own token,
              read-only)      read-only)  + reason           idempotent op id
                     └──────── runs.sqlite: runs · stages · approvals · operations ────────┘
```

* **Investigate** is an agent. It reads tickets and config with four read-only aira-ops tools and returns
  a *proposal*, a JSON object that must match the `PROPOSAL` contract.
* **Review** is a second agent, told not to trust the first. It re-checks every claim with the same
  read-only tools and returns `approve | revise | block`.
* **The gate** is a person. `approve` needs a name and a reason. Approving a proposal the reviewer blocked
  or asked to revise also needs `--override`. The decision is stored as a row in `approvals`, together with
  the SHA-256 of the exact change the person saw.
* **Apply** is plain code, not an agent. It checks the *decision record* (not the status field), refuses if
  the proposal changed after the decision, stores an operation id *before* it sends the request, and uses a
  write token that no agent ever sees. Running it twice is safe.

## How the Java version maps to the Python lab

| Python lab | Java lab | Notes |
|---|---|---|
| `SdkRunner` (Claude Agent SDK → Claude Code CLI) | `GatewayAgentRunner` (in `day4-common`) | A Messages-API tool loop against the training gateway. The agent's final answer arrives through a `submit_result` tool whose input schema *is* the contract. It is validated, and a contract error goes back to the model to fix. `max_turns` is 14 and the budget is $0.40 per stage, as in Python. |
| MCP server started with `AIRA_OPS_READONLY=1`, allow-listed tools, `tools=[]`, `strict_mcp_config`, `setting_sources=[]` | `AiraOpsTools`: the same four read tools (`search_tickets`, `get_ticket`, `lookup_account`, `get_config`) over plain HTTP | The agent can only call what is in the tool list sent to the model: four read tools and `submit_result`. It has no Bash, no file access and no write tools. `test_stage_tools_are_read_only` checks this. |
| `scrub_agent_environment()` removes write/admin tokens before an agent starts | `run`/`resume` **refuse to start** while `AIRA_OPS_APPLY_TOKEN` or `AIRA_OPS_TOKEN` is set | Java cannot remove a variable from its own environment, so the rule is enforced by refusing. `GatewayAgentRunner` refuses too, as a backstop. The Java agent is not a subprocess, so it never inherits an environment at all. |
| `pipeline.py` / `store.py` / `contracts.py` / `agents.py` | `Pipeline` / `Store` / `Contracts` (common) / `Agents` | Same tables, columns, statuses, prompts (copied verbatim, and checked by a test), error messages and span names. `python3 ../common/trace_view.py` reads Java traces. |
| `graph_langgraph.py` (LangGraph `StateGraph` + `interrupt()`) | **not ported** | LangGraph is a Python library, and the Java lab has no dependency that plays the same role. The idea is already in the pipeline: every stage is checkpointed in SQLite, the gate is a durable row, and any later process can resume or apply. This is what `interrupt()` + `SqliteSaver` give you. Read the Python file to compare. |
| `test_pipeline.py` (starts a real aira-ops) | `PipelineTest` / `CliTest` / `RunsControllerTest` | Fully offline. A `FakeRunner` plays the agents and `AiraOpsStub` (a JDK `HttpServer`) plays aira-ops' write API: per-caller tokens, `Idempotency-Key` replay, `expected_version`, latency. The test names match the Python ones. |
| — | `serve` / `serve --apply` REST API | The Spring part of the lab. See [REST API](#rest-api-the-spring-value-add). |

## Prerequisites

* **JDK 21** and **Maven 3.9+** (`java -version`, `mvn -version`).
* The repo-root **`.env`** with the gateway URL and your key (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
  optionally `LAB_MODEL`). This is the same file every other lab uses. You only need it for live runs.
* **aira-ops from Day 3** running on port 8150 (`day3-integration-security/aira-ops`). You only need it for
  live runs and `apply`. It needs `python3`, standard library only.
* **Run every command from the repo root.** The paths below and the hints the program prints assume this.
  The program finds the repo root itself, so the database and fixtures resolve from any folder.

## Build

From the repo root:

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab51 -am package -DskipTests     # builds labkit + common + lab51
mvn -q -pl day4-orchestration-evals-cicd/java/lab51 -am test                    # offline tests: no model, no network
```

This produces `day4-orchestration-evals-cicd/java/lab51/target/lab51.jar`. Define a shortcut so the commands
below stay short:

```bash
# macOS / Linux (bash, zsh) - from the repo root
J="$PWD/day4-orchestration-evals-cicd/java/lab51/target/lab51.jar"; lab51() { java -jar "$J" "$@"; }
```

```powershell
# Windows PowerShell - from the repo root
$J = "$PWD\day4-orchestration-evals-cicd\java\lab51\target\lab51.jar"; function lab51 { java -jar $J @args }
```

The program's own hints (`next:`, `retry:`) print the long form, `java -jar day4-orchestration-evals-cicd/java/lab51/target/lab51.jar ...`,
which works from the repo root. Each command starts Spring, which takes a second or two.

**Where the state lives:** `PIPELINE_DB` if set, otherwise `day4-orchestration-evals-cicd/java/lab51/runs.sqlite`
(gitignored). It uses the same schema as the Python lab's `runs.sqlite`, but it is a different file.
Traces go to `traces/lab5-1-<run>.jsonl` at the repo root, or to `LAB_TRACE_DIR` if set.

## 1. The gate without a model ($0, required)

A live run may or may not be blocked; the reviewer decides. To practise the refusals every time, replay a
real blocked run (36cc478fce, 25 Sep) from its saved stage outputs. This needs no model, no token and no
aira-ops. The fixture path resolves relative to `lab5-1-handoff/`.

```bash
lab51 replay fixtures/blocked-36cc478fce.json
```
```
run 1a2b3c4d5e replayed from fixtures/blocked-36cc478fce.json
run 1a2b3c4d5e · ACC-1001 · needs_rework · $0.0
  question: Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked.  [replay of 36cc478fce]

[investigate] done · attempt 1 · 0 tool calls · $0.0
  {
    "diagnosis": "The ingest backlog reported in T-1001 ...",
    ...
    "proposed_change": {
      "action": "update_config",
      "key": "ingest.max_concurrent_jobs",
      "value": 16,
  ...
[review] done · attempt 1 · 0 tool calls · $0.0
  {
    "verdict": "block",
  ...
```

Use your own run id (`1a2b3c4d5e` above) in the commands that follow:

```bash
lab51 approve RUN_ID --by "Your Name" --reason "backlog is P1"
#   refused: the reviewer blocked this proposal; approving it needs --override and a reason      (exit 1)

lab51 reject RUN_ID --by "Your Name" --reason "reviewer is right"
#   ...
#   [gate] reject by Your Name: reviewer is right

lab51 apply RUN_ID
#   AIRA_OPS_APPLY_TOKEN is not set - the apply step has its own credential (java -jar ... tokens)

AIRA_OPS_APPLY_TOKEN=x lab51 apply RUN_ID
#   refused: run RUN_ID has no approval on record

lab51 reject RUN_ID --by "Someone Else" --reason "me too"
#   refused: run RUN_ID was already decided
```

PowerShell has no `VAR=x command` form. Set the variable for the one call, then remove it:

```powershell
$env:AIRA_OPS_APPLY_TOKEN="x"; lab51 apply RUN_ID; Remove-Item Env:AIRA_OPS_APPLY_TOKEN
```

**The override path.** Replay again to get a fresh run, then approve with `--override` and apply against a
running aira-ops:

```bash
lab51 replay fixtures/blocked-36cc478fce.json                       # new RUN_ID
lab51 approve RUN_ID --by "Your Name" --reason "memory fix confirmed on T-1001" --override
#   [gate] approve by Your Name (OVERRIDE): memory fix confirmed on T-1001

# SHELL 2 only - the one that holds the write token (see section 2 for tokens)
lab51 apply RUN_ID
#   run RUN_ID · ACC-1001 · applied · $0.0
#   ...
#   [apply] done · op 6f1c...-... · update_config {"action": "update_config", "key": "ingest.max_concurrent_jobs", "value": 16, ...}

lab51 apply RUN_ID          # again: a no-op. Same output, nothing is written twice.
```

This changes aira-ops' config (4 → 16 on your local seed data). Restart aira-ops with `--reset` if you want
the seed back.

**Inspect:**

```bash
lab51 show RUN_ID
lab51 list
#   1a2b3c4d5e  ACC-1001  applied            $0.0     Ingest backlog on T-1001: slides queued since 06:00, patholo
python3 day4-orchestration-evals-cicd/common/trace_view.py --latest lab5-1-RUN_ID
#   - pipeline.advance  3 ms  account="ACC-1001"
#     - stage.investigate  0 ms  attempt=1 cost_usd=0.0 tool_calls=0 turns=0
#     - stage.review  1 ms  attempt=1 cost_usd=0.0 tool_calls=0 turns=0
#     - gate.waiting  0 ms  verdict="block"
#   - gate.decided  0 ms  decision="approve" approver="Your Name" override=true
#   - apply  20 ms  action="update_config" op_id="..." approver="Your Name" http_status=200 replayed=false
```

## 2. Tokens

aira-ops needs per-caller tokens: one that only reads, scoped to the account, for the agents, and one that
may write, for apply. `tokens` shells out to `python3 day3-integration-security/aira-ops/aira_ops.py
--issue-token`, exactly like the Python lab. Set `PYTHON` if your interpreter is not called `python3`, or
`python` on Windows.

```bash
lab51 tokens --account ACC-1001
```
```
# Tokens are shown once; callers.json keeps only their hashes. Restart aira-ops with --callers.
export AIRA_OPS_READ_TOKEN=...
export AIRA_OPS_APPLY_TOKEN=...
# then restart aira-ops (same AIRA_OPS_TOKEN as before):
#   python3 "/abs/path/to/day3-integration-security/aira-ops/aira_ops.py" --callers "/abs/path/to/.../callers.json"
```

On Windows the command also prints `$env:AIRA_OPS_READ_TOKEN="..."` lines. The tokens are secrets: do not
paste them into chat, tickets or commits.

**Terminal layout for live runs.** Use three shells, because each process gets only the credential it needs:

| Shell | Runs | Holds |
|---|---|---|
| 0 | aira-ops (`python3 .../aira_ops.py --callers ...`) | `AIRA_OPS_TOKEN` (admin) |
| 1 | `run`, `show`, `approve`/`reject`, `resume`, `serve` | `AIRA_OPS_READ_TOKEN` only |
| 2 | `apply`, `serve --apply` | `AIRA_OPS_APPLY_TOKEN` only |

If shell 1 still has `AIRA_OPS_TOKEN` or `AIRA_OPS_APPLY_TOKEN` exported, `run` refuses:

```
refusing to start the agents: AIRA_OPS_TOKEN is set in this process. A process that runs agents holds no write or
admin token - unset it in this shell (unset AIRA_OPS_TOKEN  /  Remove-Item Env:AIRA_OPS_TOKEN) and run `apply` from another shell.
```

## 3. Run it live (costs money)

```bash
# SHELL 1 - agents (read token only)
export AIRA_OPS_READ_TOKEN=...                 # PowerShell: $env:AIRA_OPS_READ_TOKEN="..."
lab51 run --account ACC-1001 --question "Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked."
#   run 5f1ffe5e78 started
#   run 5f1ffe5e78 · ACC-1001 · awaiting_approval · $0.2314        (or needs_rework / no_change; ids, costs and counts vary)
#   [investigate] done · attempt 1 · 7 tool calls · $0.1102 ...
#   [review] done · attempt 1 · 6 tool calls · $0.1212 ...
lab51 show RUN_ID
lab51 approve RUN_ID --by "Your Name" --reason "why"        # or reject; a block/revise needs --override
lab51 resume RUN_ID     # after a failed stage: finished stages are not re-run or re-paid; never writes

# SHELL 2 - the write (starts no agent)
export AIRA_OPS_APPLY_TOKEN=...                # PowerShell: $env:AIRA_OPS_APPLY_TOKEN="..."
lab51 apply RUN_ID
```

If the reviewer blocks, repeat the two refusals from section 1 on your run. If it approves, decide with a
reason and apply from shell 2.

When a stage fails, for example when the model cannot produce a valid proposal, the failure is recorded
along with what it cost:

```
stage failed (recorded, finished stages kept): review: the agent could not produce output matching the contract after 3 attempts
  trace:  python3 day4-orchestration-evals-cicd/common/trace_view.py --latest lab5-1-RUN_ID
  retry:  java -jar day4-orchestration-evals-cicd/java/lab51/target/lab51.jar resume RUN_ID
```

`resume` re-runs only the unfinished stage. The failed attempt's cost stays in the total. After an approve,
`resume` prints `next: ... apply RUN_ID   (in a shell that holds AIRA_OPS_APPLY_TOKEN)`.

If apply times out, or aira-ops is down or returns a 5xx, the status becomes `outcome_unknown`. Run `apply`
again: it sends the **same operation id** as its `Idempotency-Key`, so aira-ops applies the change at most
once. Any other 4xx, such as a version conflict or a read token used for a write, ends in `apply_failed`.

Environment variables: `AIRA_OPS_URL` (default `http://127.0.0.1:8150`), `AIRA_OPS_READ_TOKEN`,
`AIRA_OPS_APPLY_TOKEN`, `PIPELINE_DB`, `APPLY_TIMEOUT_S` (default 8), `LAB_TRACE_DIR`, and `.env` for the gateway.

## REST API (the Spring value-add)

The same pipeline over HTTP. Every endpoint calls the same `Pipeline` methods as the CLI, so the gate rules,
refusals and messages are identical. The CLI and a server can share `runs.sqlite`.

**Two servers, never one.** A server that holds both tokens would put the write credential in the same
process as the agents, which is exactly what the Python lab's environment scrub prevents. So there are two
modes:

| Start | Port | Holds | Can | Refuses |
|---|---|---|---|---|
| `lab51 serve` | 8170 | `AIRA_OPS_READ_TOKEN` | create runs (stages run **synchronously**: the request returns when the run reaches the gate, typically 1–3 minutes live, so give your HTTP client a long timeout), resume, replay, show, approve/reject | `apply` (403). It also refuses to *start* if `AIRA_OPS_APPLY_TOKEN` or `AIRA_OPS_TOKEN` is set. |
| `lab51 serve --apply` | 8171 | `AIRA_OPS_APPLY_TOKEN` | show, approve/reject, **apply** | anything that starts a stage (403). It refuses to start without the apply token. |

Both servers bind to `127.0.0.1` only, and `--port N` overrides the default. There is no authentication:
`"by"` is a typed name, like the CLI's `--by`. This is a classroom simplification. In production, an
authenticated and authorised identity sits in front of the gate.

```bash
# shell 1
lab51 serve
curl -s localhost:8170/api/health
curl -s -X POST localhost:8170/api/replay                                    # {"id": "...", "status": "needs_rework", ...}
curl -s -X POST localhost:8170/api/runs/RUN_ID/approve -H 'Content-Type: application/json' \
     -d '{"by": "Your Name", "reason": "backlog is P1"}'
#   409 {"error": "refused: the reviewer blocked this proposal; approving it needs --override and a reason"}
curl -s -X POST localhost:8170/api/runs/RUN_ID/approve -H 'Content-Type: application/json' \
     -d '{"by": "Your Name", "reason": "memory fix confirmed", "override": true}'
curl -s -X POST localhost:8170/api/runs/RUN_ID/apply
#   403 {"error": "refused: this is the agents server: it holds no write token and never writes. ..."}
curl -s -X POST localhost:8170/api/runs -H 'Content-Type: application/json' \
     -d '{"account": "ACC-1001", "question": "Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked."}'
curl -s localhost:8170/api/runs
curl -s localhost:8170/api/runs/RUN_ID

# shell 2
lab51 serve --apply
curl -s -X POST localhost:8171/api/runs/RUN_ID/apply                         # {"status": "applied", "operation": {...}}
```

PowerShell: use `curl.exe` (not the `curl` alias), or `Invoke-RestMethod`:

```powershell
Invoke-RestMethod -Method Post http://localhost:8170/api/replay
Invoke-RestMethod -Method Post http://localhost:8170/api/runs/RUN_ID/reject -ContentType 'application/json' `
    -Body '{"by": "Your Name", "reason": "reviewer is right"}'
```

| Endpoint | Body | Result |
|---|---|---|
| `GET /api/health` | | `{"mode": "agents" \| "apply", "db": ...}` |
| `GET /api/runs` | | the 20 latest runs, with cost |
| `GET /api/runs/{id}` | | run + `stages` + `approval` + `operation` |
| `POST /api/runs` | `{account, question}` | 201, run at the gate. On a failed stage: 502 `{error, run, retry, trace}` |
| `POST /api/runs/{id}/resume` | | finishes unfinished stages and never writes |
| `POST /api/replay` | `{fixture?}` (default `fixtures/blocked-36cc478fce.json`) | 201, $0 |
| `POST /api/runs/{id}/approve` | `{by, reason, override?}` | 409 on any gate refusal |
| `POST /api/runs/{id}/reject` | `{by, reason}` | |
| `POST /api/runs/{id}/apply` | | apply server only |

Errors: 409 `refused: ...` (gate or contract), 404 `refused: 'no run X'`, 403 (wrong server or missing
credential), 400 (bad input).

## Your tasks (the four TODO regions in `Pipeline.java`)

The solution is in place. Each exercise region is marked, in the same four places as the Python
`starter/pipeline.py`:

```java
// >>> TODO 1: checkpoint - a finished stage is never run (or paid for) twice
...
// <<< TODO 1
```

1. **Checkpoint** (`runStage`): a finished stage is never run, or paid for, twice.
2. **The gate** (`decide`): who decides, why, one decision only, and a block needs an override.
3. **No approval on record, no write** (`apply`): check the decision record, not the status field.
4. **Operation id before send** (`apply`): so a timeout followed by a retry writes at most once.

To practise, delete the code between a pair of markers, replace it with
`throw new UnsupportedOperationException("TODO n");`, and run the tests until they pass again:

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab51 -am test
# or one test:  mvn -q -pl day4-orchestration-evals-cicd/java/lab51 -am test -Dtest=PipelineTest#test_resume_skips_finished_stages -Dsurefire.failIfNoSpecifiedTests=false
```

Which tests catch which TODO:

| TODO | Tests |
|---|---|
| 1 | `test_resume_skips_finished_stages`, `a_failed_stage_says_how_to_retry_and_resume_does_not_re_pay` |
| 2 | `test_decision_needs_a_name_and_a_reason_and_happens_once`, `test_a_reviewer_block_needs_an_explicit_override`, `test_a_reviewer_revise_is_not_an_approval`, the CLI/REST walkthroughs |
| 3 | `test_the_gate_trusts_the_decision_record_not_the_status_field`, `test_the_saved_blocked_run_replays_and_the_gate_holds` |
| 4 | `test_unknown_outcome_is_retried_with_the_same_operation_id`, `a_5xx_is_an_unknown_outcome_and_apply_again_is_safe` |

The live test (`LiveTest`, two real stages, costs money) runs only with `LAB_LIVE=1` and
`AIRA_OPS_READ_TOKEN` set: `LAB_LIVE=1 mvn -q -pl day4-orchestration-evals-cicd/java/lab51 -am test -Dtest=LiveTest -Dsurefire.failIfNoSpecifiedTests=false`.
