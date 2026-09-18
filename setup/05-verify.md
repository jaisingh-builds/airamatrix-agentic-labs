# Verify

```bash
cd airamatrix-agentic-labs
cp .env.example .env     # then paste your key into ANTHROPIC_AUTH_TOKEN
make doctor
```

You want `FAIL 0`. Then:

```bash
make test        # offline checks, no cost
make cost        # your budget for today
```

If `make doctor` passes but a lab fails, that is the lab doing its job — the
starter code has TODOs in it. See the lab's README.
