---
description: Explain an unfamiliar module in a way you can actually check
---

Explain the module at `$1`.

Work from the code, not from what the names suggest. Report, in this order:

1. **What it is for**, in one sentence.
2. **The public surface** — what other code can call, and what each entry point
   returns.
3. **Each block of the main method**, in order: what it computes, and which
   inputs reach it.
4. **State** — anything held between calls, and who mutates it.
5. **Callers** — search the repository. Name every caller you find, and say
   which behaviour each one appears to depend on.

Then, separately and clearly labelled:

**Claims I have not verified.** Anything above that is inference rather than
something you read directly — intent, "this is probably a bug", "this is
dead code", assumptions about callers outside this repository. Be honest and
specific here; this section is what the reader is going to check.

Do not suggest changes, do not refactor, and do not edit anything. If you
think something is wrong, put it in the unverified list with the input that
would demonstrate it.
