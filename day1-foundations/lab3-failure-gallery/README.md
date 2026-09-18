# Lab 1.3 — Failure gallery

**Time:** 30 minutes
**Cost:** nothing. No model is called.

---

## The idea

Five agent failures, all of which have happened in production, all reproduced
*deterministically* on your machine. Real models fail intermittently — you cannot
learn from a bug you can only reproduce one time in five.

`naive_loop.py` is a working agent loop with five defects. The tests are your
specification.

```bash
cd day1-foundations/lab3-failure-gallery
python3 test_gallery.py
```

Five failures. Fix `naive_loop.py` until all five pass. Do not edit the tests or
`fake_gateway.py`.

## The five

| # | Failure | What it looks like in production |
|---|---|---|
| 1 | **No stopping condition** | The agent never finishes. It does not crash — it bills. Nobody notices until the invoice. |
| 2 | **Assistant turn dropped** | Results are sent for a request that was never recorded. The model sees answers to questions it has no memory of asking. |
| 3 | **Errors reported as success** | A tool failed; the model was never told. The agent confidently reports work it did not do. |
| 4 | **Identical call repeated** | Same tool, same arguments, again and again. Each repeat is a full round-trip that changes nothing. |
| 5 | **Results split across messages** | Works fine. Also quietly teaches the model to stop calling tools in parallel — so your agent gets slower and you never find out why. |

Failure 3 is the one to take seriously. The others cost money or time; that one
produces a confident, wrong answer, which is the failure mode that reaches a
customer.

Failure 5 is the one people argue about. It is not an error — nothing breaks.
That is exactly why it survives code review.

## Hints, if you want them

- Failure 1: `while True` is the bug. What replaces it?
- Failure 2: something is appended to `messages` too late, or not at all.
- Failure 3: the `tool_result` block has a field you are not setting.
- Failure 4: you need to remember what has already been called. Two identical
  calls is a coincidence; three is a loop.
- Failure 5: count how many `messages.append` calls happen per turn.

## Done when

- [ ] All five tests pass
- [ ] You can explain, for each one, what it would look like in a real system
- [ ] You have decided which of the five your own Lab 1.1 agent is still vulnerable to

## Then go back to Lab 1.1

Your agent from Lab 1.1 has at least one of these. Find it and fix it. That is
the point of the gallery — not these five tests, but the habit of asking "which
of these is my agent doing right now?"
