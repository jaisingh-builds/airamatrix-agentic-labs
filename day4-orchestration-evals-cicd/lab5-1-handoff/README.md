# Lab 5.1 — Multi-agent handoff: two stages, shared state, a human gate

```
 question ─▶ investigate ─▶ review ─▶ [ human gate ] ─▶ apply (plain code)
             (agent,         (agent,     approve/reject     one write, own token,
              read-only)      read-only)  + reason           idempotent op id
                     └──────── runs.sqlite: runs · stages · approvals · operations ────────┘
```

* **Investigate** (Claude Agent SDK) reads tickets and config through the Day 3 MCP
  server and returns a *proposal* as JSON against `contracts.PROPOSAL`.
* **Review** is a second agent told not to trust the first: it re-checks every
  claim with the same read-only tools and returns `approve | revise | block`.
* **The gate** is a person. `approve` needs a name and a reason; approving a
  blocked proposal also needs `--override`. The decision is a row in `approvals`.
* **Apply** is not an agent. It checks the *decision record* (not the status
  field), stores an operation id *before* sending, and uses a write token no
  agent ever sees. Running it twice is safe.

Least privilege, three times: agents get a caller token scoped to one account and
read-only; the MCP server is started with `AIRA_OPS_READONLY=1`, no built-in tools,
`strict_mcp_config`, no settings sources, `permission_mode="dontAsk"`; and the
**process** that starts agents holds no write token. The SDK starts Claude Code with
`{**os.environ, **options.env}` - `options.env` adds, it cannot remove - so `run` and
`resume` drop every secret-named variable except the gateway key before the first agent
starts, and `SdkRunner` refuses to start while a write/admin token is present. `apply`
starts no agent and is the only command that reads `AIRA_OPS_APPLY_TOKEN`.
`test_the_agent_subprocess_environment_holds_no_write_or_admin_token` proves it with a
stub `claude` that records the environment it was given.

The approval is bound to the proposal: `approvals.proposal_sha` is the hash of the change
the human saw, and `apply` refuses if the proposal changed since. `--by` is a typed name -
a classroom simplification; production uses an authenticated, authorised identity.

## Run it (live)

```bash
# aira-ops from Day 3 must be running (port 8150), with per-caller tokens:
python3 pipeline.py tokens --account ACC-1001      # prints the read and the apply token, once

# SHELL 1 - agents (read token only). Use starter/pipeline.py to run YOUR code.
export AIRA_OPS_READ_TOKEN=...
python3 pipeline.py run --account ACC-1001 --question "Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked."
python3 pipeline.py show RUN_ID
python3 pipeline.py approve RUN_ID --by "Your Name" --reason "why"      # or reject
python3 pipeline.py resume RUN_ID          # after a failed stage: finished stages are not re-run or re-paid; never writes

# SHELL 2 - the write (starts no agent)
AIRA_OPS_APPLY_TOKEN=... python3 pipeline.py apply RUN_ID

python3 ../common/trace_view.py --latest lab5-1-RUN_ID
python3 graph_langgraph.py --print-graph    # the same flow as a LangGraph StateGraph with interrupt()
python3 graph_langgraph.py --account ACC-1001 --question "..." --db graph.sqlite   # durable: stops at the gate
python3 graph_langgraph.py --db graph.sqlite --thread T --decide approve --by NAME --reason "..."   # any later process
```

`graph_langgraph.py` without `--db` uses `InMemorySaver`: pause and resume must happen in
one process. With `--db` (`SqliteSaver`) a new process resumes at the gate, and a crashed
apply re-runs with the same operation id (`test_graph` restarts it three times). Its write
token is read from a file inside `apply()` only, never from the environment.

What happened when we ran it (25 Sep 2026, ACC-1001):

| run | investigate proposed | reviewer | human | result |
|---|---|---|---|---|
| 36cc478fce | revert cap 4 → 16 | **block** — overrides a memory mitigation with no evidence it is resolved; "212 slides" misquoted | reject | nothing written |
| 1597824a97 | 4 → 8, "INC-88 shipped" (said only in the question) | **block** — INC-88 exists nowhere in aira-ops | — | nothing written |
| 5f1ffe5e78 | 4 → 8, after platform-lead recorded the load test on T-1001 | review failed (schema) → `resume` → **approve** | approve | config v2 = 8, audit: `pipeline-apply`, verified |

## Your tasks (starter/pipeline.py)

1. **Checkpoint** — a finished stage is never run (or paid for) twice.
2. **The gate** — who, why, one decision, block needs override.
3. **No approval on record, no write** — check the decision record.
4. **Operation id before send** — so a timeout + retry writes at most once.

```bash
LAB51_TARGET=starter python3 -m unittest test_pipeline    # fails until you finish
python3 -m unittest test_pipeline test_graph              # the reference: all pass
```
