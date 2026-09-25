# Step 04 — Gateway + Policy: tools as MCP, authorized on every call

**Goal.** One MCP endpoint that exposes the aira-ops API and the ops handbook as tools, and a Cedar policy
engine that decides — per caller, per tool, per argument — whether a call may happen.

```
Gateway $AC_PREFIX-ops-gateway         inbound: JWT from the step-3 pool (our 4 clients only)
  target ops-read    OpenAPI (GET ops)                 -> aira-ops, READ token from the token vault
  target ops-write   add_ticket_comment,               -> aira-ops, WRITE token from the token vault
                     set_ingest_concurrency
  target handbook    Lambda -> Bedrock Knowledge Base (the Day 3 ops handbook)
Policy engine (ENFORCE)   default deny · forbid always wins
  01 read_tools        permit ops-read___* and handbook___search_handbook   if scope has aira-ops/read
  02 comment           permit ops-write___add_ticket_comment                if scope has aira-ops/comment
  03 config_change…    permit ops-write___set_ingest_concurrency            if scope has aira-ops/config AND value <= 16
  04 hard_ceiling      forbid set_ingest_concurrency for everyone           if value > 16
```

The write token sits *behind* the gateway. Whether a caller may use a write tool is decided by Cedar,
from the caller's token and the tool's arguments — never by the agent's prompt.

## Do it

```bash
export AIRA_OPS_URL=...  AIRA_OPS_READ_TOKEN=...  AIRA_OPS_WRITE_TOKEN=...  KB_ID=...   # from the trainer
PYTHONPATH=.. python setup_gateway.py
```

It creates the Lambda, the API-key providers, the policy engine, the gateway and its targets, loads
`policies/*.cedar`, and then **tests every role** against the live gateway:

```
investigator: tools/list shows 7        get_config -> ALLOWED   add_ticket_comment -> DENIED
supervisor:   tools/list shows 8        add_ticket_comment -> ALLOWED        set 16 -> DENIED
approver:     tools/list shows 1        set 32 -> DENIED (hard_ceiling)      set 16 -> 409 from aira-ops
```

The approver's `set 16` uses a deliberately stale `expected_version` (999): the policy **permits** it,
and aira-ops then refuses it with `409`. That proves the permit without changing the shared config.

## Check it

AgentCore console → **Gateways** → targets and inbound auth; **Policy** → the engine, four `ACTIVE`
policies. Each denial names its determining policy (`hard_ceiling-…`) or says "No policy applies".

## Talk about it

- **`tools/list` is filtered per caller.** The investigator does not *see* `add_ticket_comment`. An agent
  cannot be talked into calling a tool it has never been shown — and if it tries, policy denies it anyway.
- **Arguments are policy inputs.** `context.input.value <= 16` is a business rule enforced outside the model.
  The 16 comes from the handbook (`runbook-ingest-backlog.md`: "do not raise above 16 without capacity team sign-off").
- **Forbid wins.** Even the approver cannot set 32 through this gateway. A higher value is a different,
  human process.
- Read the `.cedar` files aloud — a policy is readable by a security reviewer who never reads Python.

## Gotchas

- An OpenAPI **property** named `body` is treated as the entire request body (`request body is not valid
  JSON`). The comment field is called `comment`.
- The validator rejects a `forbid` with an unscoped principal as "overly restrictive" — scope it with
  `principal is AgentCore::OAuthUser`. The script deletes and recreates a `FAILED` policy.
- Each run posts a `[policy test]` comment on T-1005 — harmless, and proof the supervisor's permit works.
