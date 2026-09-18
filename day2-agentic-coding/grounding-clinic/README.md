# Lab 3b — Grounding clinic

**Time:** 30 minutes

Write the context that makes an agent useful on *your* codebase, then measure
whether it changed anything.

## 1. Pick a target

Your own repository if you have it checked out; otherwise `legacy-svc`.

## 2. Measure before

Pick a task the agent will get *wrong* without context. Good candidates:

- "Add a new endpoint following our conventions" — which conventions?
- "Write a test for X" — in which framework, in which directory, named how?
- "Fix this build" — with which command?

Run it. Save the output. This is your baseline.

## 3. Write a CLAUDE.md

Use [this repo's `CLAUDE.md`](../../CLAUDE.md) as a worked example. It is short
on purpose.

What belongs in it:

- how to **build and test** — the exact command, including the flags that are
  easy to get wrong
- conventions that are **not discoverable from the code** in one read
- things that will **actively mislead** an agent otherwise (a dead directory, a
  legacy module nobody should copy, a naming rule with an exception)

What does not belong:

- anything the agent can read from the code in a few seconds
- your architecture diagram
- aspirations. Write what is true today, not what you wish were true.

> The test of a line in CLAUDE.md: *would the agent have got this wrong without
> it?* If not, delete the line. A long CLAUDE.md is not a better one — it is
> context you pay for on every single request.

## 4. Add two slash commands

Two tasks your team does repeatedly. See `.claude/commands/` for the shape.
Good candidates: run the tests the way your CI does; review a diff against your
standards; scaffold a module the way your team lays them out.

## 5. Add one hook

One thing you want enforced, not requested. Formatting, or a secret scan. See
`.claude/hooks/block-secrets.sh`.

The difference matters: asking politely in CLAUDE.md works most of the time. A
hook works every time. Anything that must not happen belongs in a hook.

## 6. Measure after

Re-run the same task from step 2. What changed?

## Done when

- [ ] A CLAUDE.md that earns every line
- [ ] Two slash commands your team would actually use
- [ ] One hook that enforces something
- [ ] A before/after you can show someone

## Take it with you

This is the most portable thing in the whole programme. Repository context files,
prompt discipline and task scoping transfer to any agentic coding tool. The
specific syntax does not.
