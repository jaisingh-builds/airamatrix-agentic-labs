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

| Record | Never record | Keep for |
|---|---|---|
| ids (run, ticket, op), stage names, tool names and argument *shapes* | tokens, keys, `Authorization` headers — masked at the sink by name, by shape and by value | 14 days in CI artefacts; delete with the run |
| status, error class + message, attempt, duration, turns | full ticket bodies and patient/customer data (cut to 600 chars; prefer ids) | incidents: until the post-mortem closes |
| cost, token counts, model | prompts with PII; the model's full output if it can contain PII | evals: results JSON, not raw transcripts |
