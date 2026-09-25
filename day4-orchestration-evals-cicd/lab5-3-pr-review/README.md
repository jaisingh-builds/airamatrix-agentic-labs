# Lab 5.3 — Agent-assisted PR review as a pipeline stage

`review.py` runs **headless Claude Code** (`claude -p`) over a diff and turns its
answer into an exit code a pipeline can gate on.

```
git diff base...head ─▶ secrets scan (regex) ─▶ size cap ─▶ redact ─▶ claude -p ─▶ verify findings ─▶ exit code
                          blockers, no model      fail closed             --json-schema      on a changed line?
                                                                          Read/Grep/Glob     quotes that line?
```

| Exit | Meaning | In CI |
|---|---|---|
| 0 | no blocking findings | check passes |
| 2 | at least one verified `blocker` | check fails → merge blocked |
| 1 | could not review (error, budget, diff too big, `is_error`) | check fails — **fail closed** |

Artefacts: `review.json` (machine) and `review.md` (the PR comment). The model never
posts; a separate CI job with the only write token posts `review.md`.

The headless call (see `claude_cmd()`):

```bash
claude -p --output-format json --json-schema "$SCHEMA" --system-prompt "$SYSTEM" \
  --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --permission-mode dontAsk \
  --strict-mcp-config --setting-sources "" --max-turns 12 --max-budget-usd 0.5 < prompt
```

Its environment is **allowlisted**: PATH/HOME/LANG plus the gateway URL and key.
`GITHUB_TOKEN`, `AIRA_OPS_TOKEN` and anything else in CI never reach it.

## Try it

```bash
python3 -m unittest test_review -v                     # every control, a stub `claude`, no cost
python3 demo_prs.py --base day4 --worktree /tmp/d4wt   # five realistic PR branches, nothing pushed
python3 review.py --repo /tmp/d4wt --base day4 --head demo/apply-retry --out /tmp/rv/apply-retry
```

What happened (25 Sep 2026, claude-sonnet via the gateway):

| branch | tests | review | exit | cost |
|---|---|---|---|---|
| demo/trace-errors-only | pass | no findings | 0 | $0.28 |
| demo/apply-retry | 2 fail | blocker: new idempotency key per retry → duplicate writes | 2 | $0.25 |
| demo/drop-approval-check | 1 fails | blocker + major: gate no longer checks the decision record | 2 | $0.18 |
| demo/workshop-env | pass | blocker by pattern; token redacted before the model saw the diff | 2 | $0.12 |

`demo/workshop-env` is the one tests can't catch — and the reason secrets are
found by code, not by the model.

## Your tasks (starter/review.py)

1. **An allowlisted environment** for the subprocess.
2. **A failed run is a failure** — check the exit code *and* `is_error`.
3. **A finding is a claim** — keep it only if it points at a changed line and quotes it.

```bash
LAB53_TARGET=starter python3 -m unittest test_review    # fails until you finish
```
