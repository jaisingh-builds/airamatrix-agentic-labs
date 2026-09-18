# Lab 3 — Brownfield: comprehension, characterisation, then change

**Time:** 75 minutes (extended — the highest-value session for a team maintaining
an existing product)

> If a sanitised copy of your own repository is available, use that instead.
> `legacy-svc` exists so the lab runs regardless.

---

## The situation

`legacy-svc` bills slide analysis. It was written for one customer in 2021 and
extended since. It has **no tests**. The nightly invoice run is crashing.

You have: the code, a stack trace, and a defect report with three complaints in it.

## Step 1 — Comprehension (15 min)

Before changing anything, get a trustworthy explanation. In Claude Code:

```
/explain-module day2-agentic-coding/brownfield/legacy-svc
```

or just ask it to explain `BillingEngine.calculateInvoice`.

**Then verify what it told you.** This is the discipline the programme is about:
an explanation you have not checked is a hypothesis. Pick two claims it made and
confirm them against the code yourself.

## Step 2 — Characterisation tests FIRST (25 min)

Open `BillingEngineCharacterisationTest`. Two tests are written; the rest is
yours.

A characterisation test pins what the code **does**, not what it should do —
bugs included. That is the point. Without it, you cannot tell a refactor from a
regression.

```bash
mvn -q test -pl day2-agentic-coding/brownfield/legacy-svc -am
```

Use the agent to generate these — it is good at enumerating cases. Then read
every one. An agent-written test that asserts the wrong current behaviour is
worse than no test, because you will trust it.

## Step 3 — Triage the defect report (10 min)

Three things were reported. **They are not all real.** Work out which reproduce
before you fix anything:

1. The nightly crash
2. "Zero-slide accounts fail"
3. "The rounding is wrong"

Your characterisation tests are how you check. One of the three does not
reproduce at all. Finding that out is worth more than fixing it would have been.

## Step 4 — Fix (20 min)

Fix what is real. For each change:

- the characterisation tests tell you what else you moved
- if a characterisation test now fails, that is a behaviour change — was it
  deliberate?
- `calc(...)` is used by an old batch job and INT-4471 says do not delete it.
  Does your fix change what that caller sees?

## Step 5 — Review note (5 min)

Write it in `review-note.md` using the template in `../metrics/`. Three things:

- what you accepted from the agent
- what you **rejected**, and why
- what a human still has to verify that no test covers

That note is your Day 2 checkpoint artefact.

## Done when

- [ ] Characterisation tests cover every tier, rush, and prepaid credits
- [ ] You can say which of the three reported symptoms was not real
- [ ] The crash is fixed and the batch-job caller still behaves
- [ ] You decided the rounding question deliberately, and wrote down why
- [ ] `review-note.md` exists

## The trap

The agent will offer to rewrite `calculateInvoice` into something much tidier. It
will probably be tidier. Accepting that before you have characterisation tests is
exactly how brownfield refactors go wrong — and it is the single most common
failure mode of agentic coding on real systems.
