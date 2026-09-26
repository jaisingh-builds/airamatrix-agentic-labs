# Step 01 — Observability: turn on Transaction Search

**Goal.** Make every span from Runtime, Gateway, Memory and your agent code land in CloudWatch, where
GenAI Observability shows it and AgentCore Evaluations (step 8) reads it.

**Why first.** Evaluations score *traces*. If traces are not flowing before the agents run, there is nothing
to score and nothing to debug with. It is one switch per account and region — the trainer has already
flipped it in the training account; running it again is harmless.

## Do it

```bash
bash enable.sh
```

It makes three calls:

1. `logs put-resource-policy` — lets X-Ray write into the `aws/spans` log group.
2. `xray update-trace-segment-destination --destination CloudWatchLogs` — retried until the policy applies.
3. `xray update-indexing-rule` at 100 % — every trace is searchable (fine for training; production samples).

**Expected:** a table showing `Destination: CloudWatchLogs`, `Status: ACTIVE`.

## Check it

CloudWatch console → **Application Signals → Transaction search** is enabled.
After step 7 you will see traces under **GenAI Observability → Bedrock AgentCore** — one per agent run,
with a span for every model call, tool call and gateway hop.

## Talk about it

- Instrumentation costs you nothing in the agent code: step 6 starts the agent with
  `opentelemetry-instrument main.py`, and Strands emits GenAI semantic-convention spans.
- A trace answers "what did the agent *do*", which the final answer never tells you. Lab 5.2 graded the
  trajectory for the same reason.

**Gotcha.** In zsh, `$ACC:l…` is a modifier (`:l` = lowercase) — `enable.sh` always writes `${ACC}`.
