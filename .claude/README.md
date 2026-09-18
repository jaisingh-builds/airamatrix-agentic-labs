# .claude/ — shared, non-secret agent configuration

This directory is **committed**. Everyone who clones gets it.

**Your key does not go here.** It goes in `~/.claude/settings.json`, which is
yours alone. Putting a key in this file shares it with the whole class — and
with anyone who ever clones this repo.

| File | What |
|---|---|
| `settings.json` | Gateway URL, pinned models, permission rules, hook wiring |
| `commands/` | Slash commands for tasks this repo does repeatedly |
| `agents/` | Subagent definitions — narrow jobs, focused context |
| `hooks/` | Deterministic gates. Formatting and secrets are enforced, not requested. |

Day 2 Lab 3b has you write these for your own repository. Read them as examples.
