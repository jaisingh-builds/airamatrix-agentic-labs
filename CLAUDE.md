# AiraMatrix Agentic Labs

Training repository for the 4-day Agentic AI & Agentic Coding programme.

> This file is itself Day 2 teaching material. Read it as an example of what
> belongs in a CLAUDE.md — and what does not.

## What this repo is

Lab exercises in three languages. Participants implement the `TODO(lab)` sections;
reference solutions live on the `solutions` branch only.

## Layout

- `labkit/` — shared plumbing (gateway client, tracer, budget, cost). **Not** a
  framework. It deliberately contains no agent loop: writing that is Lab 1.1.
- `day1-foundations/` … `day4-*/` — one directory per day, one per lab.
- Reference solutions are on the **`solutions` branch**, never on `main`.

## Conventions

- **Python and Node lab exercises use the standard library only.** No
  `pip install`, no `npm install`. Twenty-two laptops behind a corporate proxy is
  not the place to discover a dependency problem. This is about the exercises in
  this repository; it is not a rule about the participant's own projects, and a
  team working in their own checked-out scaffold is outside it.
- **Java builds through the root POM.** Always `-am`, or the `labkit` dependency
  will not resolve:
  `mvn -q test -pl day1-foundations/lab1-bare-metal-loop/java -am`
- **Every lab has offline tests that always run, and live tests gated on
  `LAB_LIVE=1`.** Live tests call the model and cost money. Never make a live
  test run by default.
- **Secrets live in `.env` (gitignored) or `~/.claude/settings.json`.** Never in
  the repo. A pre-commit hook blocks the obvious cases.

## When editing labs

- A starter must **fail** its own tests until implemented. If a change makes the
  starter pass, the lab is broken.
- A solution must **pass** the lab's own tests unmodified. The trainer kit
  verifies this; solutions are not present in this repo.
- Keep the three languages at parity in behaviour and in error messages. A
  participant comparing with their neighbour should see the same thing.

## What is deliberately not here

Model IDs, gateway URLs and budgets are **not** hardcoded anywhere in lab code.
They come from `.env` via `labkit`. Changing the model for the whole room is a
gateway-side edit, not 22 file edits.
