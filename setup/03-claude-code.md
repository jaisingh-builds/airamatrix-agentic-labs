# Claude Code

Needed from **Day 2**.

## Install

```bash
npm install -g @anthropic-ai/claude-code
```

## Point it at the training gateway

Put this in `~/.claude/settings.json` (on Windows:
`%USERPROFILE%\.claude\settings.json`). Create the file if it does not exist.

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://GATEWAY-HOST",
    "ANTHROPIC_AUTH_TOKEN": "sk-PASTE-YOUR-KEY-HERE",
    "ANTHROPIC_MODEL": "claude-sonnet",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1"
  }
}
```

Copy in [templates/claude-settings.user.json](templates/claude-settings.user.json).

> **Put your key here, never in the repo's `.claude/settings.json`.**
> That file is committed and shared with everyone who clones.

## Check it

```bash
claude
```

Then `/status`. You should see:

- a **Base URL** line showing the training gateway, and
- an **Auth token** line naming `ANTHROPIC_AUTH_TOKEN`.

If you see a `Login method` line naming a claude.ai account instead, the
variables are not reaching Claude Code.

Send it a message. A reply means you are done.

## Trust the workspace (do this once)

The first time you open the lab repo, run `claude` **interactively** in it and
accept the trust dialog.

Until you do, Claude Code ignores the repo's `.claude/settings.json` — its
permission rules *and* its model pinning. Symptom: it tries to reach a model your
key does not allow and you get

```
403 key not allowed to access model ... Tried to access claude-opus-4-8
```

This is why your key and model pins go in `~/.claude/settings.json` above, not
only in the repo file. The repo file is a convenience once trusted; the user file
is what actually makes things work.

> **It has to be your own `~/.claude/settings.json`.** Putting these values in
> the repository's `.claude/settings.json` does not work — Claude Code ignores
> credentials supplied by a project, so a repo you cloned cannot hijack your
> session. If you see *"OAuth session expired"*, this is why.

## Choosing a model

Three models are available to you, all from Day 1:

| Type this | You get | Relative cost |
|---|---|---|
| `sonnet` or `claude-sonnet` | Claude Sonnet 5 — **the default** | 1x |
| `opus` or `claude-opus` | Claude Opus 4.8 | ~2.5x |
| `haiku` or `claude-haiku` | Claude Haiku 4.5 | ~0.5x |

Switch inside a session:

```
/model opus
```

Or start on one:

```bash
claude --model opus
```

Both spellings work: `opus` is Claude Code's built-in alias, which the settings
above point at our gateway's `claude-opus`.

Your budget is the same whichever you pick, so Opus simply spends it ~2.5x
faster. Use it when a task deserves it — a hard refactor, a design question —
and come back to Sonnet for routine work. `make cost` shows what is left.

> **These are gateway names, not AWS ARNs.** If you have seen the long
> `arn:aws:bedrock:...` strings on your access card, those are for the Day 4
> AgentCore labs only — see [04-bedrock-agentcore.md](04-bedrock-agentcore.md).
> Never put an ARN in `ANTHROPIC_MODEL`.

## Notes for this programme

- **Pin the models.** Without `ANTHROPIC_MODEL`, Claude Code picks its own
  default, which is the most expensive one. Your daily budget will not last.
- **WebSearch does not work** on the models behind our gateway. WebFetch does.
- **Opus 4.8 is available from Day 1.** `/model` switches to `claude-opus`. It
  costs about 2.5x Sonnet, so switch deliberately rather than by default — your
  daily budget is the same either way.
