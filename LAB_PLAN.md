# Lab Plan — Process, Day by Day

This is a **process** document: what to do and in what order, for every lab.
No implementation code — that lives in the lab directories themselves, and
writing it is the point of each lab. Reference solutions are on the
`solutions` branch only.

For dates, timings and cost per lab, see [CURRICULUM.md](CURRICULUM.md). For
repo layout and setup, see [README.md](README.md).

---

## Day 1 — Agentic AI Foundations

### Lab 1.1 — Bare-metal agent loop (60 min)

1. Read why the lab exists: every framework wraps `observe → decide → act`.
   Write it by hand once so later frameworks stop looking like magic.
2. Pick your language (Python, Node, or Java) and open its starter agent file.
3. Locate the 5 TODOs inside the run loop. Everything else — tools, schemas,
   tracing, cost tracking — is already provided.
4. Implement, in order:
   - call the model with conversation + tool schemas + system prompt
   - pull `stop_reason` and tool-use blocks out of the response
   - handle the "model is done" case and return its text
   - run each requested tool and build tool-result blocks
   - append **all** results as one user message, then loop
5. Run the agent against the fixed goal (ingest-tier capacity question).
6. Run the offline tests (tools/schemas), then the opt-in live test
   (`LAB_LIVE=1`) — it costs about a cent.
7. Open the trace file this run produced in `traces/` and read what the agent
   actually did.
8. Confirm: correct answer, all three tools used, step-limit guardrail test
   passes, and you can explain why the step limit exists.

### Lab 1.2 — Tool schema design clinic (30 min)

1. Run the scorer against the deliberately bad schema and record the
   baseline numbers (steps, invalid arguments, pass rate).
2. Note that tasks likely still pass — the point is that a bad schema shows
   up as extra steps and wasted calls, not outright failure.
3. Edit your own schema file only — never the tool implementations, or
   you're no longer measuring the schema.
4. Improve it in order of payoff: add enums for constrained fields, explain
   what values mean, describe parameters (not just tools), state call order
   where one call depends on another, state what each tool returns.
5. Re-run the scorer after each change and compare against baseline and
   against the reference target (3.2 avg steps, 0 invalid args).
6. Confirm: your schema beats baseline, zero invalid arguments, and you can
   name which single change bought the most improvement.

### Lab 1.3 — Failure gallery (30 min, no model calls)

1. Run the gallery test suite against the naive loop and see which of the
   five deterministic failures currently fail.
2. Work through the five failures one at a time without editing the tests or
   the fake gateway:
   - no stopping condition
   - a dropped assistant turn
   - a tool error silently reported as success
   - an identical call repeated needlessly
   - tool results split across multiple messages
3. After each fix, re-run the suite and confirm that failure now passes
   without reintroducing an earlier one.
4. For each of the five, write down (mentally or in notes) what it would
   look like in a real production system.
5. Go back to your Lab 1.1 agent and decide which of these five it is still
   vulnerable to. Find and fix at least one.

---

## Day 2 — Agentic Coding with Claude Code

### Lab 2 — Greenfield: spec to running service (60 min)

1. **Before writing anything**, write your own time estimate for building
   this by hand. Save it — an estimate written afterwards doesn't count.
2. Read the full spec (endpoints, job shape, validation rules, status
   transitions) before involving the agent at all.
3. Ask the agent for a **plan** first. Read the whole plan. Push back on
   anything that looks wrong or presumptuous before any code is written.
4. Work in slices — one intent per change (e.g. "add the model and its
   validation" is a slice; "build the service" is not).
5. After every slice, read the **entire diff**, not just the parts that
   changed the file you were expecting.
6. Run `/review-diff` on each slice before starting the next one.
7. Pay specific attention to the rules agents tend to shortcut: the rush/
   slideCount capacity rule, and returning every failed field on rejection
   (not just the first).
8. Check that tests exist for edge cases, not just the happy path — zero,
   over-limit, negative, missing, and wrong-typed input.
9. Watch for scope creep — if the agent starts adding auth or a database
   unprompted, that's a sign it has drifted from the spec.
10. When done, compare actual time spent against your written estimate.

### Lab 3 — Brownfield: comprehension → characterisation → change (75 min)

1. **Comprehension (15 min).** Ask the agent to explain the module and the
   specific method at the center of the bug. Do not act on the explanation
   yet — pick two of its claims and verify them yourself against the code.
2. **Characterisation tests first (25 min).** Write tests that pin what the
   code currently *does*, bugs included — not what it should do. Use the
   agent to help enumerate cases, then read every generated test yourself;
   an incorrect characterisation test is worse than none, because you'll
   trust it.
3. **Triage the defect report (10 min).** Three symptoms were reported.
   Before fixing anything, use your characterisation tests to work out which
   ones actually reproduce. Expect at least one not to.
4. **Fix (20 min).** Fix only what's real. After each change, re-run the
   characterisation suite:
   - if a characterisation test now fails, that's a behaviour change —
     decide deliberately whether it was intended
   - check any caller noted as depending on old behaviour (e.g. a legacy
     batch job) still sees what it expects
5. **Review note (5 min).** Write down what you accepted from the agent,
   what you rejected and why, and what a human still needs to verify that no
   test covers. This is the Day 2 checkpoint artefact.
6. Stay alert to the trap: the agent will likely offer a tidier rewrite of
   the core method. Do not accept a rewrite before characterisation tests
   exist to catch a silent behaviour change.

### Lab 3b — Grounding clinic (30 min)

1. Pick a target repo — your own if available, otherwise the shared
   brownfield service.
2. Choose a task the agent will likely get wrong without extra context
   (following your conventions, writing a test in the right framework/
   location, running the actual build command).
3. Run that task with no added context and save the output as your
   baseline.
4. Write a CLAUDE.md containing only: the exact build/test commands
   (including easy-to-miss flags), conventions not obvious from a quick
   read of the code, and traps that would actively mislead an agent (dead
   directories, legacy modules, naming exceptions).
5. Deliberately leave out anything the agent can already read from the code,
   any architecture diagram, and any aspirational (not-yet-true) statement.
   For every line, ask: would the agent have gotten this wrong without it?
6. Add two slash commands for tasks your team repeats often (e.g. running
   tests the way CI does, reviewing a diff against your standards).
7. Add one hook for something that must be enforced every time rather than
   requested politely (formatting, secret scanning).
8. Re-run the same baseline task and compare before vs. after.
9. Confirm you have a CLAUDE.md that earns every line, two commands your
   team would actually use, one working hook, and a before/after you could
   show someone else.

---

## Day 3 — MCP, integration, security, multi-agent, evals, CI/CD

Not yet in this repo. This plan will be extended when the labs land —
`git pull` on the morning of Day 3.

## Day 4 — AgentCore and the capstone

Not yet in this repo, and requires AWS access granted on the day. This plan
will be extended when the labs land — `git pull` on the morning of Day 4.
