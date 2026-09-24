# aira-ops — the internal API every Day 3 lab talks to

A small, real HTTP service: support tickets, customer accounts, slide-analysis
jobs and a configuration store, in SQLite. Standard library only.

```bash
export AIRA_OPS_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
python3 aira_ops.py --reset          # http://127.0.0.1:8150, fresh seed data
python3 -m unittest test_aira_ops    # 17 contract tests, no network
```

The token is a secret. It lives in your shell, never in code, never in a prompt.

## Why it behaves like a real internal system

| Behaviour | Why an agent needs it |
|---|---|
| Bearer token on every call | Secrets stay with the caller, not the model |
| `Idempotency-Key` required on writes; a replayed key returns the original | An agent that retries after a timeout must not write twice |
| Config writes need `expected_version`; stale → `409` | No silent overwrite of someone else's change |
| A write can change a value, never its type | Found live: a model sent `"16"` for an integer key |
| Ticket status is a state machine; illegal move → `409` with the allowed moves | The error tells the agent what it *can* do |
| One error shape: `{"error": {code, message, retryable, hint}}` | Errors an agent can recover from |
| Every write in `/audit` with the `X-Actor` header | Who changed what, including agents |

## The data has a story in it

`T-1001` (ingest backlog) is caused by `ingest.max_concurrent_jobs`, cut from 16
to 4 the day before — and the config's description says why. An agent that
reads the ticket, its comment and the config can diagnose it. The fix is a
production write, so it must hit an approval gate.

`T-1007` contains an instruction addressed to AI assistants, embedded in
customer text. It is there so the room can watch what each design does with it.
