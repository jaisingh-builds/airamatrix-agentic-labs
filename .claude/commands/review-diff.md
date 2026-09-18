---
description: Review the working-tree diff the way Day 2 asks you to review agent output
---

Review the current `git diff` with the discipline this programme teaches.

Run `git diff` and assess, in this order:

1. **Scope.** Does the diff do one thing? A diff that does two things is two
   diffs. Say so plainly.
2. **Correctness.** What would break? Name the specific input or state.
3. **Tests.** Is the new behaviour actually covered, or only apparently covered?
   A test that passes against a stub is not coverage.
4. **Security.** Any secret, credential, or path that escapes its sandbox?
5. **What a human must verify.** The point of the review discipline is that some
   things an agent cannot confirm. List them explicitly.

End with a short verdict: **accept**, **accept with changes**, or **reject**, and
one sentence of reasoning. Do not pad. If the diff is fine, say it is fine.
