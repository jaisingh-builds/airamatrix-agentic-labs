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
| 3 | If the model is done, return its text |
| 4 | Otherwise run each requested tool and build `tool_result` blocks |
| 5 | Append the results as **one** user message and loop |

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
cd python && python3 test_agent.py      # offline: tools and schemas
cd node   && node --test
mvn -q test -pl day1-foundations/lab1-bare-metal-loop/java -am
```

The two tests that matter run the real model, and are opt-in because they cost
about a cent:

```bash
LAB_LIVE=1 python3 test_agent.py
```

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

## If you finish early

See `EXTENSION.md`.
