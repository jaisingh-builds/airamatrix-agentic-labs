# Exercise — debug a failed run from its trace alone (15 min)

`fixtures/trace-5f1ffe5e78.jsonl` is the real trace of Lab 5.1 run `5f1ffe5e78`
(25 Sep 2026). You get the trace and nothing else: no logs, no database, no
re-run.

```bash
python3 trace_view.py fixtures/trace-5f1ffe5e78.jsonl
```

Answer from the trace only:

1. Which stage failed, on which attempt, and after how long?
2. What exactly was wrong with its output? Which part of the contract did the model break?
3. Did the investigate stage run again when the run was resumed? How do you know — and what did that save?
4. Who approved the change, was it an override, and what was written, under which operation id?
5. What did the failed attempt cost? (Look carefully — then say what the trace *should* have recorded.)
6. One span's input shows `"key": "[REDACTED]"`. Is that a secret? What does it tell you about the redaction rule, and what is the cost of getting it wrong in that direction?

<details><summary>What we found</summary>

1. `stage.review`, attempt 1, 22.6 s: `ResultError … Failed to provide valid structured output after 5 attempts`.
2. The model put `verified` and `source` at the top level instead of inside each `checks` item, and left out `reasons`. Fix applied: a `description` on `checks` in the schema and one sentence in the reviewer prompt.
3. No: `stage.investigate.skipped reason="checkpoint: already done"`. The $0.084 investigate stage was not paid for twice.
4. `gate.decided approver="Jai" override=false`, then `apply … op_id=5507812a-… http_status=200 replayed=false`.
5. The failed span has no `cost_usd`. The CLI did charge for 5 attempts; the first version of the pipeline dropped that number. Now `RunnerError` carries the cost, the span records it, and the stage row accumulates it across attempts.
6. It isn't a secret: it is a config key name, redacted because the rule matched the word `key`. Over-redaction is a bug too — you can't debug what you can't read. The rule now matches `token|secret|password|api_key|auth|…_key`, not bare `key` (see `test_spans.py`).
</details>

## What goes in a trace — and what never does

`spans.py` applies these in order at the sink: **allowlist** (attribute names not in
`ALLOWED_ATTRS` become `"[dropped]"`; free text in tool arguments becomes
`"[text: N chars]"`), then **redact** (by name, shape and value), then **cut**. Error
messages are redacted before they are cut. Truncation is not redaction: a name in the
first 600 characters survives a cut.

| Record | Never record | Keep for |
|---|---|---|
| allowlisted ids (run, ticket, op), stage, tool, status, attempt, duration, turns, cost | tokens, keys, `Authorization` headers; any attribute not on the allowlist | 14 days in CI artefacts; delete with the run |
| error class + message, redacted then cut | ticket bodies, patient/customer text, free-text search terms | incidents: until the post-mortem closes |
| decision, approver id, override | the free-text reason; prompts or model output that can carry PII | eval results: secrets masked on save; handle like logs |

The fixture trace predates the allowlist, which is why it still shows raw tool arguments.
