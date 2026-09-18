# Day 2 — Agentic Coding with Claude Code

Drive real greenfield and brownfield work through Claude Code on your own stack,
with a disciplined review loop and honest measurement.

| Lab | What | Time |
|---|---|---|
| [Lab 2](greenfield/SPEC.md) | Greenfield: spec to running service, agent-driven | 60 min |
| [Lab 3](brownfield/README.md) | Brownfield: comprehension → characterisation tests → change | 75 min |
| [Lab 3b](grounding-clinic/README.md) | Write your first real CLAUDE.md, commands and a hook | 30 min |

## Before you start

```bash
make doctor
```

Then, **once per machine**: run `claude` interactively in this repo and accept
the trust dialog. Until you do, the repo's `.claude/settings.json` is ignored —
including its model pinning — and you will get a `403` about `claude-opus-4-8`.

## End-of-day checkpoint

[CHECKPOINT.md](CHECKPOINT.md) — one reviewed, merged agent-assisted change with
a written review note explaining what you accepted, rejected and why.
