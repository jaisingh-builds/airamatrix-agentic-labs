# `.claude/` — the grounding for this repository

Day 2 teaching material. This directory is the worked example: read it, then
build the equivalent for your own service.

| File | What it does |
|---|---|
| `settings.json` | Permission rules and hook wiring. **No gateway URL, no models, no key** — those live in your own `~/.claude/settings.json`, from your access card. Credentials supplied by a project are ignored by design. |
| `commands/` | Prompts you stopped retyping. Version-controlled, so they get code-reviewed. |
| `agents/reviewer.md` | A subagent with one job and a narrow tool set. |
| `hooks/` | Enforcement. A hook exits 2 and the action never happens. |

## Commands

| Command | Use it when |
|---|---|
| `/lab <n>` | What does lab n ask for, and where are its files |
| `/cost` | How much of today's budget is left |
| `/explain-module <path>` | Explain unfamiliar code **and** tell me what to check |
| `/slice <work>` | Cut work into changes you can review one at a time |
| `/characterise <target>` | Characterisation tests that pin current behaviour, bugs included |
| `/review-diff` | Review the working tree with the discipline Day 2 teaches |
| `/review-note` | Draft accepted / changed / rejected / needs-a-human, with evidence |
| `/review-claude-md` | Audit CLAUDE.md — which lines earn their place |

`/slice` and `/characterise` map directly onto Day 2's two labs. `/review-note`
produces a Day 2 deliverable.

## Hooks — enforced, not requested

| Hook | Fires on | Refuses |
|---|---|---|
| `block-secrets.sh` | Write, Edit, MultiEdit, NotebookEdit | content shaped like a credential: `AKIA…`/`ASIA…`, `sk-ant-…`/`sk-aira-…`, a keyed `aws_secret_access_key` |
| `protect-graded-tests.sh` | Write, Edit | edits to the five files that **grade** a lab |
| `block-dangerous-bash.sh` | Bash | `rm -rf ~`, `git push --force`, `git reset --hard`, `git clean -fd`, `curl \| sh`, `chmod 777` |

### What they deliberately allow

A guardrail that fires on the wrong thing gets switched off within a week, and
then you have none. Each of these is tested:

- an Edit that **removes** a credential — grep the whole payload and you block
  people from deleting secrets; the payload carries `old_string` too
- a 40-character SHA hash, and `sk-PASTE-YOUR-KEY-HERE` in `.env.example`
- `git push --force-with-lease`, which is the safe form the block message
  recommends
- `rm -rf build/`, and your own test files anywhere outside the graded five

### Overriding, deliberately

```bash
LAB_ALLOW_TEST_EDITS=1     # you found a real bug in a graded test
```

There is no override for the other two. If one fires wrongly, that is a bug —
tell the trainer rather than working around it.

## The point of all this

Three things ask, three things enforce:

- **CLAUDE.md asks.** It is context, and context can be ignored under pressure.
- **A command asks**, and makes the asking repeatable.
- **A hook enforces.** The model never gets the chance.

Put a rule in CLAUDE.md when you want judgement applied. Put it in a hook when
you want the answer to be the same every time.
