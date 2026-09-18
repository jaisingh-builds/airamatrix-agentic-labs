# VS Code

## Extensions

Open the repo in VS Code. It will offer the recommended extensions from
`.vscode/extensions.json` — accept them.

## Gateway credentials for the Claude Code extension

**This is the step people miss.** The VS Code extension checks credentials
*before* it launches, and it reads them from VS Code's own settings — not from
`~/.claude/settings.json`. If you only set the shell variables, the extension
will still ask you to log in.

Open the command palette and run **Preferences: Open User Settings (JSON)**, then
add:

```json
"claudeCode.environmentVariables": [
  { "name": "ANTHROPIC_BASE_URL", "value": "https://GATEWAY-HOST" },
  { "name": "ANTHROPIC_AUTH_TOKEN", "value": "sk-PASTE-YOUR-KEY-HERE" },
  { "name": "ANTHROPIC_MODEL", "value": "claude-sonnet" },
  { "name": "ANTHROPIC_DEFAULT_SONNET_MODEL", "value": "claude-sonnet" },
  { "name": "ANTHROPIC_DEFAULT_OPUS_MODEL", "value": "claude-opus" },
  { "name": "ANTHROPIC_DEFAULT_HAIKU_MODEL", "value": "claude-haiku" },
  { "name": "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "value": "1" }
]
```

There is a copy in [templates/vscode-settings.json](templates/vscode-settings.json).

Restart VS Code afterwards.

## Why `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`

Through a gateway, Claude Code sends its full capability set. Some of those
fields are not accepted by the Bedrock models behind our gateway, and you would
see `400 Extra inputs are not permitted`. This setting turns them off. It costs
you nothing in the labs.
