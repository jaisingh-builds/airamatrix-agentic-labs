# Day 2 — Agentic Coding with Claude Code

Drive real greenfield and brownfield work through Claude Code on your own stack,
with a disciplined review loop and honest measurement.

| Lab | What | Time |
|---|---|---|
| [Lab 2](greenfield/SPEC.md) | Greenfield: spec to running service, agent-driven | 60 min |
| [Lab 3](brownfield/README.md) | Brownfield: comprehension → characterisation tests → fix → refactor | 75 min |
| [Lab 3b](grounding-clinic/README.md) | Write your first real CLAUDE.md, commands and a hook | 30 min |

## Before you start

```bash
make doctor
```

Then, **once per machine**: run `claude` interactively in this repo and accept
the trust dialog. The dialog gates the settings that *grant* capability —
`permissions.allow` and `additionalDirectories` — so until you accept it, this
repo's allow-list does not apply and you will be prompted for commands the team
already agreed to. The `deny` and `ask` rules and the hooks apply either way.

**Separately**, pin your model. A `403` naming `claude-opus-4-8` is the unpinned
`opus` alias resolving to Claude Code's built-in default, which the gateway does
not serve. Pin all four aliases in your own `~/.claude/settings.json` — never in
this repo, which deliberately hardcodes no model IDs:

```
ANTHROPIC_MODEL=claude-sonnet
ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet
ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus
ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku
```

**Do not put an inference profile ARN in `ANTHROPIC_MODEL`.** Those are Day 4,
for the AgentCore labs that call Bedrock directly. Here they produce a `400`.

## End-of-day checkpoint

[CHECKPOINT.md](CHECKPOINT.md) — one reviewed, merged agent-assisted change with
a written review note explaining what you accepted, rejected and why.
