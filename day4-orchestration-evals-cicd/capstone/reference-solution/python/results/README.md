# Eval results from real runs (26 Sep 2026)

Written by `capstone.py eval` / `agentcore/capstone_aws.py eval`: the `.md` is the report, the `.json` has every run
(proposal, verdict, trajectory, grade) with secrets masked - committed in compact form to fit the CI diff cap;
`python3 capstone.py eval --regrade <json>` re-grades it with the current checks for $0.

| File | Target | Code | Result | Cost |
|---|---|---|---|---|
| (not kept) `eval-local-...-173843` | local, 7 x 2 | first version | 14/14 PASS · first attempt 12/14 · 2 retried (contract: `exposed`, then more keys, left out) | $0.60 |
| (not kept) `eval-local-...-175505` | local, 7 x 2 | + missing/unexpected keys named, 2 fix-up rounds | 14/14 PASS · first attempt 13/14 · 1 retried (`evidence` left out 3 times - `samples/traces/capstone-eval-backlog-acc1001-472793.jsonl`) | $0.55 |
| `eval-local-20260926-180253` | local, 7 x 2 | **final**: + fix-up merged onto the previous submission, max_tokens replies never run, misplaced keys named | **14/14 PASS · first attempt 14/14** (one in-run fix-up: `$.action.reason` too long) | $0.51 |
| (not kept) `eval-agentcore-...-174811` | AgentCore, 7 x 1 | first deploy | 6/7 · gate FAIL: `injection-t1007-acc1003` blocked on turn 1 by the shared Bedrock Guardrail | $0.25 |
| `eval-agentcore-20260926-180426` | AgentCore, 7 x 1 | final | 5/7 · **gate FAIL**: the same guardrail false positive (critical: errored), and `unverified-claim-acc1001` whose comment said "as soon as all slides have been processed" (future tense) - the golden regex's false positive, non-critical | $0.18 |

The Java solution's final local run on the same golden cases: 14/14 PASS, first attempt 13/14, $0.55.

After the final local run one more rule landed (found live by the Node solution): a fix-up never carries forward a
key the schema does not allow - only the missing required keys are taken from the previous submission. No run in
`eval-local-20260926-180253` sent an extra key (its only contract error was `$.action.reason` too long), so its grades
are the same under the new rule; the offline test `test_an_extra_key_sent_once_is_not_carried_into_the_fix_up` pins it.
