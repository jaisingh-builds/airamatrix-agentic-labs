# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `doctor`: cannot reach the gateway | Wi-Fi, or the proxy is blocking it | Check you are on the training Wi-Fi. Give IT [ALLOWLIST.md](ALLOWLIST.md). |
| `401` / key rejected | Key truncated when copied, or wrong variable | Re-copy the whole key. It goes in `ANTHROPIC_AUTH_TOKEN`, not `ANTHROPIC_API_KEY`. |
| `429` | Daily budget spent, or too many requests at once | `make cost`. Budgets reset each morning. Ask the trainer if you are genuinely out. |
| `400 Extra inputs are not permitted` | Claude Code sending fields Bedrock rejects | Set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` — see [02-vscode.md](02-vscode.md). |
| Claude Code asks me to log in | Variables are not reaching it | For the terminal: `~/.claude/settings.json`. For VS Code: `claudeCode.environmentVariables`. They are different places. |
| `/status` shows a claude.ai account | Same as above | Same as above. |
| Labs use the wrong endpoint | An exported `ANTHROPIC_BASE_URL` beats `.env` | `unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN`, then re-run. `doctor` warns about this. |
| Java: `labkit:jar:1.0.0 was not found` | Built the lab without its dependency | Always pass `-am`: `mvn -q test -pl <module> -am` |
| `Address already in use` on 8137 | A fixture server is already running | Harmless — the labs reuse it. Kill it if you want: `lsof -ti:8137 \| xargs kill` |
| `403 key not allowed to access model ... claude-opus-4-8` | The repo's `.claude/settings.json` is ignored because the workspace is untrusted, so model pinning did not apply | Run `claude` interactively in the repo once and accept the trust dialog. Make sure the pins are also in `~/.claude/settings.json`. |
| `Ignoring N permissions.allow entries ... not been trusted` | Same cause | Same fix. Harmless on its own, but it means the env block is not applying either. |
| `Failed to authenticate: OAuth session expired` | Claude Code is trying a stale claude.ai login instead of the gateway | The gateway config must be in **`~/.claude/settings.json`** (your own user file). Credentials placed in the repo's `.claude/` are deliberately ignored — a cloned repo is not allowed to inject credentials. |
| `model not found` / `400` naming a model | An ARN or a raw Bedrock id was put in `ANTHROPIC_MODEL` | Use `claude-sonnet`, `claude-opus` or `claude-haiku`. ARNs are Day-4 AgentCore only. |
| `403 key not allowed to access model` naming a model you did not pick | A model pin is missing, so Claude Code reached for its own default | Set all four: `ANTHROPIC_MODEL`, and the `ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL` pins. |
| `/model opus` says the model is unavailable | `ANTHROPIC_DEFAULT_OPUS_MODEL` not set, so `opus` resolves to a model the gateway does not serve | Add `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus`. Or type `/model claude-opus` directly. |
| Budget disappearing fast | Opus is ~2.5x Sonnet per token | `make cost`. Switch back with `/model sonnet`. |
| Day 4: `AccessDeniedException` on `global.anthropic.*` | Calling the shared model id directly is denied by design | Use **your own** profile ARN from your card. |
| Day 4: `AccessDeniedException` with "explicit deny in a permissions boundary" | You used a model outside the approved set, or a region other than ap-south-1 | Approved: Sonnet 5, Haiku 4.5, Opus 4.8 — via your own ARNs. |
| Day 4: `aws sts get-caller-identity` fails | `AWS_PROFILE` not exported, or the key was copied short | `export AWS_PROFILE=airamatrix`, then re-check the card. |
| WebSearch returns nothing in Claude Code | Not supported on Bedrock | Use WebFetch, or the MCP tools from Day 3. |
