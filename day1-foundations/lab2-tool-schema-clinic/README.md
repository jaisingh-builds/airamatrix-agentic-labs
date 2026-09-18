# Lab 1.2 — Tool schema design clinic

**Time:** 30 minutes
**Language:** none — you are editing JSON. The lesson is language-neutral.

---

## The claim being tested

> The tool schema *is* the prompt. The model never sees your implementation —
> only the name, the description and the JSON Schema.

You are going to measure how much that is worth, on identical implementations.

## Run the baseline

```bash
cd day1-foundations/lab2-tool-schema-clinic
python3 score.py schemas/bad.json
```

Three tools with honest but useless descriptions (`"sets priority"`), no enums,
no guidance. Five tasks. Note the numbers.

## What you will probably see

**All five tasks still pass.** That surprises people, and it is the real lesson.

The tools return *recoverable* errors — when the agent invents a priority of
`"urgent"`, the tool replies `'urgent' is not a valid priority. Use one of: low,
medium, high, critical`, and the agent corrects itself.

So a bad schema does not usually show up as failure. It shows up as:

- **more steps** — every wrong guess costs a full model round-trip
- **invalid tool arguments** — attempts that did nothing but cost money
- **latency and spend** that nobody traces back to a description

That is why `score.py` reports steps and invalid arguments, not just pass/fail.
A success-rate-only metric would have told you the schema was fine.

## Your turn

Edit `schemas/yours.json`. Do not touch `tickets.py` — the implementations must
stay identical, or you are measuring the wrong thing.

Things worth trying, roughly in order of payoff:

1. **Add an `enum`** to `priority` and `status`. The model cannot invent a value
   that is not in the list.
2. **Say what the values mean.** "critical is the highest level" is what connects
   the user saying *"top concern"* to the value `critical`.
3. **Describe the parameter, not just the tool.** Give an example id.
4. **State the order.** "Use find_ticket first to get the id — never guess an id."
5. **Say what the tool returns.** An agent that knows `find_ticket` returns the
   current priority does not need a second call to check it.

## Measure

```bash
python3 score.py schemas/bad.json schemas/yours.json
```

A reference solution reaches **3.2 steps average and 0 invalid arguments**, from
a 4.4 / 1 baseline. Beat it if you can.

## Done when

- [ ] `schemas/yours.json` scores fewer average steps than `bad.json`
- [ ] Zero invalid tool arguments
- [ ] You can name which single change bought the most improvement

## Carry this into Day 3

Lab 4.1 is the same exercise against tools that *write* to real systems, where an
invalid argument is not a retry — it is an incident. The habit you build here is
the one that matters there.


---

## Reference clinic

[`reference/clinic.py`](reference/README.md) runs the same measurement against a
deliberately broken contract and a good one:

```
                    bad.json     good.json
  passed                 3/5           5/5
  steps (avg)            6.6           2.4
  invalid args            25             0
  cost               $0.1284       $0.0515
```

That is the severe case — a schema that does not expose what the implementation
needs. The lab you just did is the subtle case: correct names, vague
descriptions, **passes anyway**, and still costs a third more steps.

Outright failure gets fixed. A contract that works but overcharges you never
does.
