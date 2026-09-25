# Step 03 — Identity: who each agent is, and what it may ask for

**Goal.** Give each agent its own identity with the *least* scope it needs, and keep every secret out of
the agent's code, zip and environment.

```
Cognito user pool  $AC_PREFIX-agents         resource server "aira-ops"
  scopes   aira-ops/read      aira-ops/comment      aira-ops/config
  clients  investigator: read     reviewer: read     supervisor: read + comment
           approver: config   <- a HUMAN's client. No agent ever gets this scope.

AgentCore Identity: one OAuth2 credential provider per agent ($AC_PREFIX-<role>-oauth)
  the client secret lives in the token vault; the runtime asks for a token by provider name
```

## Do it

```bash
PYTHONPATH=.. python setup_identity.py
```

Expected: the pool, four `client <role> <id> scopes=...` lines, three `provider` lines. The approver's
secret is written to `out/approver.json` (mode 0600) — only `07-run/approve.py` reads it.

## Check it

- Cognito console → your pool → **App clients**: four clients, each with its own allowed scopes.
- Bedrock AgentCore console → **Identity** → **Outbound auth**: three OAuth providers.

## How an agent gets a token (step 6)

```python
@requires_access_token(provider_name=PROVIDER, scopes=SCOPES, auth_flow="M2M")
async def gateway_token(*, access_token: str) -> str:
    return access_token
```

At run time the Runtime gives the agent a *workload access token* for its own workload identity; the
decorator exchanges it at the token vault for a Cognito token for that provider. The agent never sees a
client secret. The Runtime only mints the workload token when the caller passes `runtimeUserId`
(see step 7).

## Talk about it

- **Roles are identities, not prompts.** "You are read-only" in a system prompt is a request; a token
  without the `comment` scope is a fact.
- The human approver is a separate identity on purpose: approving is an act by a person, recorded as that
  person — not an agent flag.
