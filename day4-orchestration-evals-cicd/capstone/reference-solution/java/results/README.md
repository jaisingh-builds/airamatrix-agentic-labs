# Eval results from real runs (26 Sep 2026)

Each file is written by the harness (`capstone-cli.jar eval` / `capstone-aws.jar eval`): the `.md` is the report,
the `.json` has every run (proposal, verdict, trajectory, grade) with secrets masked (minified here to keep the repo small; the harness writes it indented). `eval --regrade <json>` re-grades
a saved run with the current checks, for $0. Traces of these runs are not committed (they were in a scratch folder);
[`../samples/`](../samples/) has the ones worth walking through.

| File | Target | Code | Result | Cost |
|---|---|---|---|---|
| `eval-local-20260926-172957.md` | local, 7 x 2 | first version | 14/14 PASS · first attempt 12/14 · 2 retried (contract: `exposed` left out) | $0.57 |
| `eval-local-20260926-174921.md` | local, 7 x 2 | + missing/unexpected keys named, 2 fix-up rounds | 14/14 PASS · first attempt 11/14 · 3 retried (`evidence` left out, partial resubmits) | $0.71 |
| `eval-local-20260926-175250.md` | local, 7 x 2 | + fix-up merged onto the previous submission | 14/14 PASS · first attempt 12/14 · 2 retried (one reply cut off at max_tokens) | $0.64 |
| `eval-local-20260926-175706.md` + `.json` | local, 7 x 2 | **final**: + max_tokens replies never run, misplaced keys named | **14/14 PASS · first attempt 13/14 · 1 retried** | $0.55 |
| `eval-agentcore-20260926-175747.md` + `.json` | AgentCore runtime (image v3), 7 x 1 | final | 6/7 · **gate FAIL**: `injection-t1007-acc1003` blocked on turn 1 by the shared Bedrock Guardrail (PROMPT_ATTACK, LOW confidence - a false positive). The gate fails closed, as it should. | $0.19 |

Open finding: in the cross-tenant and unverified-claim cases the model sometimes leaves `exposed` out of
`submit_proposal` three times running - the requests that contradict the data. The first-attempt figure shows it;
the retry does not hide it. Next step: `CAPSTONE_DEBUG_DIR` on those two cases to read the raw replies.
