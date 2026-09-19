---
description: Draft the review note for the change you just made - accepted, changed, rejected, unverified
---

Draft a review note for the current change set.

Read the diff first: `git diff` and `git diff --staged`.

Produce exactly four sections. Every entry needs evidence — a file and line, a
test name, or a command and its output. An entry with no evidence is an opinion
and does not belong in the note.

**Accepted** — what you wrote that I am keeping as-is, and what I checked to be
comfortable with it. If I checked nothing, say so.

**Changed** — what you produced that I altered, and why. Be specific about the
defect, not the preference.

**Rejected** — what you proposed that I did not take, and the reason. This
section being empty is itself a finding: say so.

**Still needs a human** — what neither of us can settle from the code. Anything
that is a business decision rather than a code decision. Anything that depends
on a caller, a batch job, a ticket, or a number someone reconciles against.
Name who should decide it.

Finish with one line: what would have to be true for me to skip this review next
time. Be honest — the answer is usually "nothing".
