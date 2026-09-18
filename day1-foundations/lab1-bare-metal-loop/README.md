# Lab 1.1 — Build an agent loop from scratch

**Time:** 60 minutes (extended — this is the session everything else rests on)
**Languages:** Java · TypeScript/Node · Python. Pick the one you work in.

---

## Why this lab exists

Every framework you will meet later — the Agent SDK, LangGraph, AgentCore
Harness — is a wrapper around the loop you are about to write:

```
observe  ->  decide  ->  act  ->  observe  ->  ...
```

Once you have written it by hand, you can reason about what a framework is doing
for you and, more importantly, what it is doing *to* you. If you have not, every
later failure looks like magic.

## The task

An agent that answers an operational question using three tools:

| Tool | Does |
|---|---|
| `read_file` | reads a file from `workspace/` |
| `http_get` | fetches a URL (local fixture server only) |
| `calculator` | arithmetic only |

The goal it must answer:

> Fetch the ingest-tier status, read `limits.txt`, and say whether the service is
> over capacity. If it is, compute by what percentage the queue depth exceeds the
> limit, and name the escalation contact.

A correct run calls all three tools and arrives at **62.4%**.

## What you write

Open the `agent.py` / `agent.mjs` / `Agent.java` in your language. There are five
TODOs inside `run_agent`. Everything else — tools, schemas, tracing, cost — is
given to you.

| TODO | What |
|---|---|
| 1 | Call the model with the conversation, the tool schemas and the system prompt |
| 2 | Pull `stop_reason`, content blocks, and the `tool_use` blocks out of the response |
| 3 | Branch on `stop_reason` — one branch each, no "not tool_use means done" |
| 4 | Otherwise run each requested tool and build `tool_result` blocks |
| 5 | Append the results as **one** user message and loop |

### TODO 3 in one table

`stop_reason` is how the model tells you what kind of turn just ended. Treating
everything that is not `tool_use` as a finished answer is how a truncated or
refused reply gets reported to the caller as the result.

| `stop_reason` | What it means | What your loop does |
|---|---|---|
| `tool_use` | the model asked for tools | run them, append results, loop |
| `end_turn` / `stop_sequence` | the turn finished | check the text is not empty, then return it |
| `max_tokens` | the reply was cut off | `Truncated` — a half answer is not an answer |
| `refusal` | the model declined | `Refused` |
| `pause_turn` | a long turn paused | send the conversation back unchanged to resume |
| anything else | a value this code has never seen | `UnhandledStop` — fail safely, keep the trace |

## Run it

```bash
# python
cd python && python3 agent.py

# node
cd node && node agent.mjs

# java  (from the repo root)
mvn -q compile exec:java -Dexec.mainClass=com.airamatrix.lab1.Agent \
  -pl day1-foundations/lab1-bare-metal-loop/java -am
```

## Check it

```bash
cd python && python3 test_agent.py          # offline: tools, boundaries, schemas
cd python && python3 test_stop_reasons.py   # offline: the loop contract (TODO 3)
cd node   && node --test
mvn -q test -pl day1-foundations/lab1-bare-metal-loop/java -am
```

`test_stop_reasons.py` and `test-stop-reasons.mjs` script a fake model, so they
cost nothing and need no gateway. They fail until your loop is finished — they
are the specification for it.

The two tests that matter run the real model, and are opt-in because they cost
about a cent:

```bash
LAB_LIVE=1 python3 test_agent.py
```

## The workspace is hostile on purpose

`workspace/runbook.md` contains an instruction addressed to your agent, telling
it to read `credentials.txt` and send the contents to another host. The system
prompt tells the model to treat tool output as data, not instructions — that
helps, and it is not the control.

The control is in `tools.py`: `read_file` cannot leave the workspace, and
`http_get` refuses any host that is not on the allow-list. Prompts are advice;
the tool boundary is enforcement. Run your agent against the runbook and watch
what happens.

## Two rules that are easy to get wrong

**Every `tool_use` block needs a matching `tool_result`.** Miss one and the API
rejects the next request.

**All the results go back in ONE user message.** Splitting them across several
messages still works — and quietly teaches the model to stop calling tools in
parallel. You will not notice until your agent is twice as slow as it should be.

## Done when

- [ ] Your agent answers the capacity question correctly (62.4%)
- [ ] It uses all three tools
- [ ] A trace file appears in `traces/` — open it, read what your agent did
- [ ] `LAB_LIVE=1` tests pass, including the step-limit guardrail
- [ ] You can explain why the step limit exists
- [ ] `test_stop_reasons.py` passes — every stop reason has its own branch
- [ ] Pointing the agent at `runbook.md` does not produce a call to the
      exfiltration host, and you can say which line of code stopped it

## If you finish early

See `EXTENSION.md`.


---

## Reference implementation

Once your own loop passes the tests, read [`reference/agent.py`](reference/README.md),
or `git switch solutions` for the same starter with TODO 1-5 filled in.

It is the same loop with everything the slides covered: the budget reserved
before the call, a branch for every stop reason, validate → authorise → execute,
an enforced output contract, repeat detection and JSONL tracing. Standard library
only, no dependencies.

Compare three things, not style: where the budget is checked, how many stop
reasons you handle, and whether a failing tool is distinguishable from a
succeeding one.
