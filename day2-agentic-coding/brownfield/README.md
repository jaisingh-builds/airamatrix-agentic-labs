# Lab 3 — Brownfield: comprehension, characterisation, then change

**Time:** 75 minutes (extended — the highest-value session for a team maintaining
an existing product)

> If a sanitised copy of your own repository is available, use that instead.
> `legacy-svc` exists so the lab runs regardless.

---

## The situation

`legacy-svc` bills slide analysis. It was written for one customer in 2021 and
extended since. It has **no production tests** — two starter characterisation
tests are provided in step 2, and the rest are yours. The nightly invoice run is
crashing.

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

## Step 5 — Refactor, now that you can prove it (10 min)

Only now. With the characterisation tests green, the tidier rewrite the agent
offered in step 1 becomes a reasonable thing to consider — because you can show
it changed nothing.

Pick **one** thing: extract the discount ladder, or name the magic numbers. Not
both, and not the whole method. Ask for a refactor with no behaviour change, and
say the characterisation tests are the contract.

Then run them. Green means the refactor was honest. One red test means it is a
behaviour change wearing a refactor's name — read it, and do not re-baseline the
test to make it pass.

This is the order the programme teaches, and it only works this way round:
comprehension, characterisation, fix, refactor.

### The same guardrail for a version or dependency upgrade

You will do this more often than you refactor, and it is the same shape:

1. **Pin what you have.** Record current versions; get the suite green first.
2. **Ask for the compatibility read** — what breaks between these versions, and
   which of it applies to *this* code. Verify the claims. This is where an agent
   is most confidently wrong, because release notes are exactly the kind of thing
   it will summarise plausibly from memory.
3. **One upgrade per change.** A framework major and a transitive bump are two
   diffs.
4. **Let the tests find the rest.** Behaviour the release notes did not mention
   is what characterisation tests are for.
5. **Know the way back.** The revert must be one command.

## Step 6 — Review note (5 min)

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
- [ ] One behaviour-preserving refactor, proven by the same tests
- [ ] `review-note.md` exists

## The trap

The agent will offer to rewrite `calculateInvoice` into something much tidier. It
will probably be tidier. Accepting that before you have characterisation tests is
exactly how brownfield refactors go wrong — and it is the single most common
failure mode of agentic coding on real systems.
