# Lab 4.3 — An agent inside a service: streaming, cancel, timeouts, persistence

**Time:** 50 min · **Languages:** Python (server), HTML/JS (console) · **Cost:** ~$0.03 per triage run

A small web service that runs a triage agent for one account and streams its
work to a browser as it happens. Standard library only.

```bash
export AIRA_OPS_TOKEN=…            # same shell as aira-ops
python3 ../aira-ops/aira_ops.py --reset &
python3 service.py                 # http://127.0.0.1:8160
python3 -m unittest test_stream test_service   # 16 tests, fake gateway, no cost
```

Open http://127.0.0.1:8160, pick `ACC-1001`, press **Triage**.

## What an app needs that a script doesn't

| Concern | How it's handled | Test that proves it |
|---|---|---|
| Sync vs async | `POST /api/triage` returns a run id at once; the work runs on a thread | `test_run_streams_tools_and_text_then_persists_the_trace` |
| Streaming | Messages API SSE → our own SSE to the browser (`EventSource`) | `test_stream.py` on a captured real stream |
| Cancellation | `POST /api/runs/{id}/cancel`; checked between every streamed chunk | `test_cancel_mid_stream_keeps_partial_text` |
| Timeouts | Whole run `RUN_TIMEOUT_S`; stalled stream `STREAM_STALL_S` | `test_timeout_stops_a_slow_stream` |
| Retry | A stalled step is retried once, its partial text rolled back | `test_stalled_stream_is_retried_once_and_text_not_duplicated` |
| Budget | `BudgetGuard` stops the next call, not the bill | `test_budget_ceiling_stops_before_the_next_call` |
| Fallback | Every stop gives a partial answer marked *"nothing was changed"* | status on every run |
| Persistence | Runs and every event in `runs.sqlite`; click a history row (or open `/?run=<id>`) to replay a past run event by event | `GET /api/runs/{id}` |
| Crash recovery | A run left `running` by a dead process is marked `interrupted` at start-up | `test_run_orphaned_by_a_restart_is_marked_interrupted` |
| Least privilege | The agent has **read tools only** | `test_agent_has_no_write_tools` |
| Output handling | Model text rendered with `textContent`, never `innerHTML` | `static/index.html` |

Statuses a run can end in: `done`, `cancelled`, `timeout`, `step_limit`,
`budget`, `max_tokens`, `failed`, `upstream_stall` — and `interrupted`, for a run
the service was restarted in the middle of.

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
- `RUN_TIMEOUT_S=5 python3 service.py` — watch a timeout end cleanly.
- `RUN_BUDGET_USD=0.005 python3 service.py` — the budget stop.
- Restart the service. The run history is still there — click a row to replay it.
- Kill the service mid-run, start it again: that run now says `interrupted`,
  not `running` forever. (Found on the trainer machine: it said `running`.)
