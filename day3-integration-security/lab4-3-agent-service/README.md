# Lab 4.3 — An agent inside a service: streaming, cancel, timeouts, persistence

**Time:** 45 min · **Languages:** Python (server), HTML/JS (console) · **Cost:** ~$0.03 per triage run

A small web service that runs a triage agent for one account and streams its
work to a browser as it happens. Standard library only.

```bash
export AIRA_OPS_TOKEN=…            # same shell as aira-ops
python3 ../aira-ops/aira_ops.py --reset &
python3 service.py                 # http://127.0.0.1:8160
python3 -m unittest test_stream test_service   # 20 tests, fake gateway, no cost
```

Open http://127.0.0.1:8160, pick `ACC-1001`, press **Triage**.

## What an app needs that a script doesn't

| Concern | How it's handled | Test that proves it |
|---|---|---|
| Sync vs async | `POST /api/triage` returns a run id at once; the work runs on a thread | `test_run_streams_tools_and_text_then_persists_the_trace` |
| Streaming | Messages API SSE → our own SSE to the browser (`EventSource`) | `test_stream.py` on a captured real stream |
| Cancellation | `request_cancel()` sets the flag **and aborts the live stream**, so a worker blocked on a silent socket returns at once (< 1 s, tested). A tool call already in flight finishes first — at most its 8 s timeout | `test_cancel_takes_effect_while_the_model_is_silent` |
| Timeouts | Whole run `RUN_TIMEOUT_S`, enforced by a timer that aborts the stream; stalled stream `STREAM_STALL_S` | `test_timeout_fires_while_the_model_is_silent` |
| Retry | A stalled step is retried once, its partial text rolled back | `test_stalled_stream_is_retried_once_and_text_not_duplicated` |
| Budget | A **hard** ceiling: before each call the run reserves its worst case (one token per byte of request + 1,000, plus the full `max_tokens` out) and skips any call that could overshoot. A stalled attempt's usage is unknown, so its reservation is charged | `test_budget_is_a_hard_ceiling_no_call_that_could_overshoot_is_made`, `test_a_stalled_attempt_is_charged_its_reservation` |
| Fallback | Every stop gives a partial answer marked *"nothing was changed"* | status on every run |
| Persistence | Runs and every event in `runs.sqlite`; click a history row (or open `/?run=<id>`) to replay a past run event by event | `GET /api/runs/{id}` |
| Crash recovery | A run left `running` by a dead process is marked `interrupted` at start-up | `test_run_orphaned_by_a_restart_is_marked_interrupted` |
| Least privilege | The agent has **read tools only** | `test_agent_has_no_write_tools` |
| Output handling | Model text rendered with `textContent`, never `innerHTML` | `static/index.html` |

Statuses a run can end in: `done`, `cancelled`, `timeout`, `step_limit`,
`budget`, `max_tokens`, `failed`, `upstream_stall` — and `interrupted`, for a run
the service was restarted in the middle of.

## Your task: make Cancel mean now

`starter/service.py` is `service.py` with one method emptied: `request_cancel()`.
Cancel currently does nothing.

```bash
LAB43_TARGET=starter python3 -m unittest test_service     # 2 failures: the cancel tests
# write request_cancel() in starter/service.py (its docstring says what it must do)
LAB43_TARGET=starter python3 -m unittest test_service     # green
python3 starter/service.py                                  # try it in the browser
```

Setting a flag is half of it. The worker spends most of a run blocked inside a
socket read, waiting for the model's next bytes; a flag checked between chunks
cannot reach it there. `test_cancel_takes_effect_while_the_model_is_silent`
cancels during two seconds of silence and allows one.

This console has no user accounts: it is single-user, on localhost. In
production every run carries its owner's verified identity, and viewing,
streaming, replaying and cancelling a run all check it.

## What happened when it first met the real gateway

The fake-gateway tests passed. The first two live runs did not:

1. The stream **went silent for 60 seconds** mid-answer. Added a 20 s stall
   detector and one retry that discards the half-written step.
2. The next run hit **max_tokens at 1,500** and stopped mid-sentence. Raised
   the ceiling and asked for a report under 350 words.

After the fixes: ACC-1001 done in 5 steps, 7 tool calls, $0.033. ACC-1003 done
in 4 steps, $0.028 — and it named the injected instruction in T-1007 as
suspicious and did nothing with it. `GET /audit`: zero writes.

## Try

- Press **Cancel** half-way. The run ends `cancelled` with its partial text kept.
- `RUN_TIMEOUT_S=5 python3 starter/service.py` — watch a timeout end cleanly.
- `RUN_BUDGET_USD=0.06 python3 starter/service.py` — the budget stop (one call fits; the next could overshoot).
- Stop the previous server (Ctrl-C) before each one: they share port 8160.
- Restart the service. The run history is still there — click a row to replay it.
- Kill the service mid-run, start it again: that run now says `interrupted`,
  not `running` forever. (Found on the trainer machine: it said `running`.)
