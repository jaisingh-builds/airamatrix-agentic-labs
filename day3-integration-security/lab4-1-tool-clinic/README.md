# Lab 4.1 — Tool design clinic, measured

**Time:** 40 min · **Cost:** ~$0.07–0.09 per toolset run

Three badly specified tools. The same ten questions. The real model. Every tool
call executed for real against a private aira-ops. You rewrite the tools and
measure what changed.

```bash
python3 clinic.py --tools bad          # the baseline
python3 clinic.py --tools bad mine     # after your rewrite, side by side
```

## One measured run, 2026-09-25

Model `claude-sonnet` via the programme gateway, default temperature, `max_tokens=800`, `MAX_STEPS=6`,
one run per toolset over the same ten goals. It is one comparison, not a
reliability figure: repeat paired runs before claiming more.

| toolset | correct | calls / goal | tool errors | cost |
|---|---|---|---|---|
| bad  | 8/10  | 3.8 | 15 | $0.085 |
| good | 10/10 | 1.2 | 1  | $0.067 |

A second `good` run later the same morning, after adding `maxLength` and
`additionalProperties: false` to its schema: 10/10, 1.1 calls/goal, 0 errors, $0.064.

Read the middle columns, not the first. A strong model **compensates** for bad
tools by brute force: three times the calls, fifteen times the errors, and one
goal ran out of steps after fourteen guesses at a config key it had no way to
discover. On a cheaper model, or a tighter step cap, that is where it breaks.

## What to change in `mine.py`

1. Names that say what the tool does — and one job per tool.
2. Descriptions: what it returns, when to use it, when **not** to.
3. Typed, named parameters: enums for closed sets, patterns for ids, examples.
4. Errors that pass through `code`, `message` and `hint`.
5. A way to **discover** valid values instead of guessing them.

## Done when

- [ ] `mine` scores at least as well as `good` on correctness
- [ ] Your calls-per-goal and error count are both lower than `bad`
- [ ] You can name the single change that moved the numbers most

## A grader is a claim too

The first run marked *"I wasn't able to find a config key"* as correct, because
the pattern `on` matched inside the word "config". `clinic.py` now treats any
answer that gives up as a fail. Check your own evals the same way.
