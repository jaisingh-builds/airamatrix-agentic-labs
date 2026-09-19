---
description: Write characterisation tests that pin what legacy code DOES, bugs included
---

Write characterisation tests for: **$ARGUMENTS**

A characterisation test records **current behaviour**, not correct behaviour.
Bugs included. Its job is to tell a refactor from a regression later.

Rules:

- **Do not fix anything.** Not even something obviously wrong.
- If a result looks wrong, assert the wrong value and add a comment saying you
  believe it is wrong and why. That comment is the ticket.
- Cover every branch, and say which input reaches each one.
- Cover the boundaries either side of every threshold in the code, not round
  numbers you chose.
- Cover the inputs the code does not defend against: null, zero, negative,
  missing, wrong type, and any value that is absent from a lookup it trusts.
- Prefer many small named tests over one table-driven test. When one fails you
  want its name to tell you what moved.

Before writing, list the cases you intend to cover and wait for me to add to it.
Afterwards, tell me which tests would fail if someone "tidied up" the code — that
is the set actually protecting me.
