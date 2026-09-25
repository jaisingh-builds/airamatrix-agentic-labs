# aira-ops — the internal API every Day 3 lab talks to

A small, real HTTP service: support tickets, customer accounts, slide-analysis
jobs and a configuration store, in SQLite. Standard library only.

```bash
export AIRA_OPS_TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
python3 aira_ops.py --reset          # http://127.0.0.1:8150, fresh seed data
python3 -m unittest test_aira_ops    # 24 contract tests, no network
```

The token is a secret. It lives in your shell, never in code, never in a prompt.

## Why it behaves like a real internal system

| Behaviour | Why an agent needs it |
|---|---|
| Bearer token on every call | Secrets stay with the caller, not the model |
| `Idempotency-Key` required on writes; a replayed key returns the original | An agent that retries after a timeout must not write twice |
| A key is bound to its caller and exact payload; reuse with a different body → `422` | A reused key can't smuggle in a different write |
| Config writes need `expected_version`; stale → `409` | No silent overwrite of someone else's change |
| A write can change a value, never its type | Found live: a model sent `"16"` for an integer key |
| Ticket status is a state machine; illegal move → `409` with the allowed moves | The error tells the agent what it *can* do |
| One error shape: `{"error": {code, message, retryable, hint}}` | Errors an agent can recover from |
| Every write in `/audit`, marked `verified` or not | Who changed what — and whether you actually know |

## Who is calling? A header is a label; a token is an identity

With only the shared `AIRA_OPS_TOKEN`, every holder is the same caller and
`X-Actor` is whatever the caller wrote. The audit log records it with
`verified: 0` — it is a label, not proof.

Per-caller tokens fix that. The callers file stores only each token's SHA-256:

```bash
python3 aira_ops.py --callers callers.json --issue-token triage-agent --accounts ACC-1001
python3 aira_ops.py --callers callers.json --issue-token oncall-lead --write
python3 aira_ops.py --callers callers.json      # the shared token still works too
```

A verified caller's actor comes from its token (`verified: 1`); a different
`X-Actor` is ignored and kept in the entry as `claimed_actor` — evidence of an
attempt. A caller scoped to ACC-1001 gets `404` for ACC-1003's tickets,
account and jobs (a `403` would confirm they exist), sees only its own
accounts in lists, can't read `/audit`, and gets `403` on any write unless
issued with `--write`. All tested, and each control mutation-checked.

### Lab 4.4: one credential per test

| Test | Credential | Expected |
|---|---|---|
| Cross-account: `GET /tickets/T-1007` | `triage` (ACC-1001, read-only) | 404 |
| Write prevention: comment on T-1001 | same `triage` token | 403 `forbidden` |
| Actor spoofing: comment on T-1001 with `X-Actor: ceo` | `oncall-lead` (ACC-1001, `--write`) | 201, author `oncall-lead`, audit `claimed_actor: "ceo"` |

Use a write-enabled token for the spoofing test: with a read-only token the
write is refused before the actor is ever recorded, and the test passes for
the wrong reason. Verified with these exact commands on 25 Sep.

## The data has a story in it

`T-1001` (ingest backlog) is caused by `ingest.max_concurrent_jobs`, cut from 16
to 4 the day before — and the config's description says why. An agent that
reads the ticket, its comment and the config can diagnose it. The fix is a
production write, so it must hit an approval gate.

`T-1007` contains an instruction addressed to AI assistants, embedded in
customer text. It is there so the room can watch what each design does with it.
