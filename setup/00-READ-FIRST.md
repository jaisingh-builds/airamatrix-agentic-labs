# Read this first

Ten minutes, once. Do it before Day 1 if you can.

1. **Install the runtimes** — [01-prerequisites.md](01-prerequisites.md)
2. **Configure VS Code** — [02-vscode.md](02-vscode.md)
3. **Install Claude Code** — [03-claude-code.md](03-claude-code.md) *(needed from Day 2)*
4. **Verify** — [05-verify.md](05-verify.md)

AWS credentials are **not** needed for Days 1–3. You get those on Day 4 for the
AgentCore labs — [04-bedrock-agentcore.md](04-bedrock-agentcore.md).

## The one thing to get right

Everything talks to one endpoint:

```
https://<the gateway host on your access card>
```

The host is not written into this repository. It is on your access card,
alongside your key.

with **your own key**, from your access card. The key identifies you, carries your
daily budget, and is how spend is attributed. Treat it like a password.
