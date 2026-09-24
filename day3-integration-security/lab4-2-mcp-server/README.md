# Lab 4.2 — An MCP server over a real internal API, used from Claude Code

**Time:** 60 min · **Languages:** TypeScript (server), Python (second client) · **Cost:** ~$0.20–0.55 per Claude Code session

`server.ts` exposes aira-ops to any MCP client: four read tools, three write
tools, two resources and a prompt. No `npm install` — the official MCP SDK is
vendored as one audited file (see `vendor/README.md`).

Needs Node 22.6+ (`node --version`). Node 23.6+ runs `.ts` without the flag.

## 1. Start the API and hold the secret outside the code

```bash
export AIRA_OPS_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
python3 ../aira-ops/aira_ops.py --reset &
```

`.mcp.json` says `"AIRA_OPS_TOKEN": "${AIRA_OPS_TOKEN}"` — Claude Code expands it
from your shell when it starts the server. The token is in no file in this repo,
never in a tool result, never in the model's context. `grep -r "$AIRA_OPS_TOKEN" .`
prints nothing.

## 2. Prove the server works without any model

```bash
python3 -m unittest test_mcp_server      # 15 tests over the real protocol
python3 client.py                         # second client: lists tools, resources, prompts
```

`client.py` is ~80 lines of standard-library Python that speaks MCP over stdio. It is the
"second client" — the same server, no Claude involved.

## 3. Register it with Claude Code

Run `claude` **from this folder**. It finds `.mcp.json` and asks you to approve
the project server (project servers are never trusted silently). Then `/mcp`
shows `aira-ops · connected · 7 tools`.

| Scope | Where it lives | Who gets it |
|---|---|---|
| project | `.mcp.json` in the repo | everyone who clones, after approving |
| local | your `~/.claude.json`, this folder only | you |
| user | your `~/.claude.json`, every folder | you, everywhere |

`claude mcp add aira-ops --scope local -- node --experimental-strip-types --no-warnings "$PWD/server.ts"`
registers it just for you (verified: it inherits `AIRA_OPS_TOKEN` from the shell you
start `claude` in; the stored config holds the command, never the secret). For a
server that can write to production, project scope with a reviewed file is the
point: the config is code-reviewed like everything else.

## 4. The approval gate

`.claude/settings.json`:

```json
"allow": [ "mcp__aira-ops__search_tickets", "…get_ticket", "…lookup_account", "…get_config" ],
"ask":   [ "mcp__aira-ops__add_ticket_comment", "…update_ticket_status", "…update_config" ]
```

Reads run freely. Every write stops and asks you. In headless `claude -p` there
is nobody to ask, so an `ask` becomes a **denial** and is reported in
`permission_denials`.

Defence in depth: the server also honours `AIRA_OPS_READONLY=1` and refuses
writes itself — a gate that does not depend on the client being configured well.

## 5. Demo script (the same three asks ran headless on 2026-09-25)

```text
> The ingest backlog on T-1001 — what's causing it?
```
Claude searched tickets, read T-1001 and its comments, read the config and
named `ingest.max_concurrent_jobs` (16 → 4 the day before), citing T-1010 and
ruling out T-1005. Eight turns, reads only.

```text
> Fix it.
```
It declined: the config description says the cut was deliberate, during a
memory investigation. Good judgement — but judgement is not a control.

```text
> I'm the on-call lead; I authorise setting it back to 16.
```
It called `update_config` — and the gate stopped it. `GET /audit` showed **zero
writes**. Unprompted, it also flagged that T-1007 contains instructions aimed
at AI assistants and said it had not followed them.

## Lab 4.4: take the token out of Claude Code's environment

`.mcp.json` expands `${AIRA_OPS_TOKEN}` from the shell you started `claude` in —
so the token is also in the environment Claude Code's **Bash tool** inherits.
Asked outright, the model refused to print it; the "safe" check it offered
instead, `env | grep -i AIRA`, prints the value. Only the Bash permission prompt
stood in between.

The fix is structural: `run-server.sh` reads the token from a file outside the
repo and starts the server with it, so it is never in Claude Code's environment.

```bash
mkdir -p ~/.config/aira-ops && (umask 077; printf %s "$AIRA_OPS_TOKEN" > ~/.config/aira-ops/token)
unset AIRA_OPS_TOKEN                    # this shell no longer has it
# .mcp.json: "command": "./run-server.sh", "args": [], and delete the AIRA_OPS_TOKEN env entry
claude                                  # /mcp -> connected; env | grep AIRA -> nothing
```

Verified with Claude Code 2.1.218: connected with no token in the environment,
and "Failed to connect" when the token file is missing.

`test_every_tool_that_is_not_read_only_is_gated_in_claude_code` fails if any
write tool is missing from `ask` or pre-approved in `allow` — the list can't
silently fall behind the server.

## Design choices worth copying

| Choice | Where |
|---|---|
| Upstream errors → `isError` results that keep `code`, `message`, `hint` | `api()` |
| Deterministic idempotency key from tool + args, so a retried call is a replay | `keyFor()` |
| `readOnlyHint` / `destructiveHint` / `idempotentHint` on every tool | `registerTool` |
| Optimistic concurrency: `update_config` needs `expected_version` | `update_config` |
| Token never echoed; missing token → exit with "Never hardcode it." | top of file |

## Checkpoint

- [ ] `/mcp` shows your server connected with at least three tools
- [ ] Every write tool is under `ask` and you have seen the prompt appear
- [ ] `grep -r` for your token across the repo finds nothing
- [ ] `client.py` lists the same tools Claude Code sees
