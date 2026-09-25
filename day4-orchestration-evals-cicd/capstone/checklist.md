# Capstone hand-over checklist

- [ ] Branch pushed (or zipped) with a README that runs from a clean checkout
- [ ] `TEAM.md` filled in
- [ ] No secrets in code, `TEAM.md`, traces or eval results: `gitleaks detect --no-git` clean, with any
      exceptions written down in `.gitleaks.toml` (a `grep` is only a quick first look - it misses most formats)
- [ ] Guardrail: shown refusing in the demo; a test for it
- [ ] Eval: ≥ 5 golden cases, results file, pass rate and cost
- [ ] Trace: one JSONL file walked through in the demo
- [ ] Human approval point: who/why recorded; refuses without it
- [ ] Every agent has a turn limit and a budget cap
- [ ] Offline tests pass (`python3 -m unittest`)
