# Step 06 — Agents on AgentCore Runtime: one codebase, three agents

**Goal.** Deploy the investigator, reviewer and supervisor as three isolated AgentCore Runtimes — each
with its own IAM role, identity, tool view and guardrail — from one `agent/main.py`. No Docker.

```
agent/main.py            ROLE (env var) picks the prompt, the tools and the extras
  investigator   MCPClient(gateway)                          prompt: investigate, propose a change with sourced evidence
  reviewer       MCPClient(gateway)                          prompt: verify independently, approve / revise / reject
  supervisor     MCPClient(gateway) + ask_investigator + ask_reviewer + Memory
                 prompt: orchestrate; if approved, post "APPROVAL REQUESTED:" on the ticket; never change config
every agent      token from AgentCore Identity  ·  BedrockModel(guardrail_id=...)  ·  opentelemetry-instrument
```

## Read the code first (5 minutes)

Open `agent/main.py` and find:

1. `@requires_access_token(...)` — the only place a credential appears, and it is a *provider name*.
2. `MCPClient(url=GATEWAY_URL, headers={"Authorization": f"Bearer {token}"})` — the only way to the tools.
3. `BedrockModel(..., guardrail_id=..., guardrail_version=...)` — the guardrail, in code.
4. `ask_investigator` / `ask_reviewer` — agent-to-agent calls with `invoke_agent_runtime`. Note the
   `read_timeout=900, total_max_attempts=1`: an automatic retry would run the specialist **twice**.
5. `memory_manager(...)` — supervisor only.
6. `@app.entrypoint` — AgentCore Runtime calls this; `context.session_id` is the runtime session.

## Do it

```bash
PYTHONPATH=.. python deploy_agents.py              # first time: builds deps for arm64 (~1 min)
PYTHONPATH=.. python deploy_agents.py --no-build   # after editing main.py only
```

What happens:

1. `pip install --platform manylinux2014_aarch64 --only-binary=:all:` into `build/pkg` (Runtime runs on
   Graviton), plus `main.py` → `build/agent.zip` (~40 MB).
2. Upload to `s3://$AC_PREFIX-agentcore-<account>/agents/agent.zip`.
3. One IAM role per agent: invoke the model, apply *this* guardrail, fetch *its own* OAuth token, write
   logs/traces. The supervisor alone may invoke the two specialists and use the memory.
4. `create_agent_runtime` with `codeConfiguration` (`PYTHON_3_12`, entry point
   `["opentelemetry-instrument", "main.py"]`) — specialists first, then the supervisor, which gets their ARNs.

Expected: `runtime <name> READY arn:aws:bedrock-agentcore:...:runtime/<prefix>_<name>-XXXX` × 3.

## Check it

AgentCore console → **Runtime**: three agents, each with its environment variables and its own
**workload identity**. IAM → the three `$AC_PREFIX-agent-*` roles: compare the supervisor's with the others'.

## Talk about it

- **Same code, different powers.** What an agent may do comes from its role, token scope and tool view —
  all set at deploy time, none of it in the prompt.
- **microVM per session.** Each runtime session is isolated; one user's investigation cannot read another's
  memory of the process.
- **The supervisor cannot change config** even if its prompt told it to: it has no `config` scope, the
  gateway never lists the tool for it, and Cedar would deny the call.
