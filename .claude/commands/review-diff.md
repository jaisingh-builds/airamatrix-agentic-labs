---
description: Review the working-tree diff the way Day 2 asks you to review agent output
---

Review the current change set with the discipline this programme teaches.

**First, establish what you are reviewing.** `git diff` alone shows tracked,
unstaged changes only — it misses staged changes and new files entirely, which
on a greenfield task is most of the work. Run all three:

```
git status --short --untracked-files=all
git diff
git diff --staged
```

`--untracked-files=all` is not optional: without it git collapses a whole new
directory into a single line, `?? src/`, and you would review none of it. Read
every file it lists as untracked (`??`) in full. Then state
in one line which change set you reviewed, so the reader knows what you did and
did not look at.

Then assess, in this order:

1. **Scope.** Does the change do one thing? A change that does two things is two
   changes. Say so plainly.
2. **Correctness.** What would break? Name the specific input or state.
3. **Tests.** Is the new behaviour actually covered, or only apparently covered?
   A test that passes against a stub is not coverage. Check the negative and
   boundary cases, not just the happy path.
4. **Security.** Any secret, credential, or path that escapes its sandbox? Any
   new dependency that was not asked for?
5. **What was removed.** Deletions are the least-read part of a diff and the
   most dangerous: a dropped check breaks no test that never covered it, so
   deletions need reading, not just running.
6. **What a human must verify.** The point of the review discipline is that some
   things an agent cannot confirm. List them explicitly.

End with a short verdict: **accept**, **accept with changes**, or **reject**, and
one sentence of reasoning. Do not pad. If the change is fine, say it is fine —
an invented objection is worse than none.
