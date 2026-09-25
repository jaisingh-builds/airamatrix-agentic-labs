# Step 10 — Teardown

```bash
PYTHONPATH=.. python teardown.py         # dry run: lists what it will delete
PYTHONPATH=.. python teardown.py --yes   # deletes
```

Deletes, in reverse dependency order and only what **your** `out/state.json` records: dashboard → online
evaluations and their role → runtimes and their roles → the S3 bucket → memory → gateway targets and
gateway → policies and policy engine → API-key providers → gateway role → handbook Lambda and its role →
OAuth providers → Cognito pool → guardrail. Each item is best-effort; anything already gone is skipped.

Left alone on purpose: Transaction Search (account-wide, shared) and the Knowledge Base (the trainer's).
Log groups age out on their own retention; delete `/aws/bedrock-agentcore/runtimes/<prefix>_*` by hand if you want them gone now.

Afterwards `out/state.json` is renamed to `out/state.deleted.json` and `out/approver.json` is removed.
