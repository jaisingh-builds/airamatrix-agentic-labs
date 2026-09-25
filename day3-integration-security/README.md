# Day 3 — Tools, MCP, integration and security

Every lab here runs against **aira-ops**, a small internal API (tickets,
accounts, jobs, configuration) that behaves like the real thing: auth,
idempotency keys, version checks, a state machine, an audit log. Start it once:

```bash
export AIRA_OPS_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
python3 aira-ops/aira_ops.py --reset
```

| Lab | What | Time |
|---|---|---|
| [aira-ops](aira-ops/README.md) | The internal API everything talks to | — |
| [4.1 Tool clinic](lab4-1-tool-clinic/README.md) | Rewrite three bad tools; measure calls, errors, correctness | 40 min |
| [4.2 MCP server](lab4-2-mcp-server/README.md) | TypeScript MCP server, used from Claude Code and a Python client, approval gate on writes | 60 min |
| [4.3 Agent in a service](lab4-3-agent-service/README.md) | Streaming, cancel, timeouts, budget, persistence, read-only tools | 50 min |
| [4.4 Red team](lab4-untrusted-web/README.md) | An agent that reads three websites it must not trust | 45–60 min |

```bash
make day3-test      # every Day 3 offline suite, no gateway calls
```

**On Windows:** see [bootstrap/WINDOWS.md](../bootstrap/WINDOWS.md), where one PowerShell script does setup, start and tests.

## Checkpoint

Each team demonstrates an MCP server serving at least three tools to Claude
Code, with an approval gate on every write operation and secrets held outside
the codebase.
