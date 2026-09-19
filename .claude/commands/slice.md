---
description: Cut a piece of work into changes you can actually review, one at a time
---

Plan how to deliver: **$ARGUMENTS**

Do not write any code yet.

Propose a slicing into changes I can review one at a time. For each slice give:

- **what it adds** — in one sentence, as an outcome not a task list
- **what it depends on** — by slice number; nothing may depend forwards
- **rough diff size** — lines added/changed
- **how I would know it works** — the test or the command that proves it

Order them so the build is green after every slice.

Then check your own slicing against these three questions and tell me honestly
where it fails:

1. Can I read the diff in one sitting?
2. Can I test this slice on its own?
3. If it turns out wrong, will I know which part?

If a slice is over roughly 150 lines, or fails any of the three, split it and say
why. End by naming the slice you would start with and what you would need from
me before starting.
