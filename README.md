# Agentic AI & Agentic Coding — Lab Repository

AiraMatrix · 18, 19, 25, 26 September 2026 · Thane, Mumbai
Trainer: Jai Singh

---

## Start here (10 minutes)

```bash
git clone <this repo>
cd airamatrix-agentic-labs
cp .env.example .env
```

Open `.env` and paste the key from your **access card** into `ANTHROPIC_AUTH_TOKEN`.
Then:

```bash
make doctor
```

Every line should say OK. If something says FAIL, the line underneath tells you
what to do. If you are still stuck after one attempt, ask — do not spend the
morning on it.

Then open [day1-foundations/lab1-bare-metal-loop/README.md](day1-foundations/lab1-bare-metal-loop/README.md).

---

## What you were given

| Thing | Where |
|---|---|
| A gateway key (`sk-...`) | Your access card. It is yours; do not share it. |
| A daily budget | USD 14 per day, reset every morning. `make cost` shows what is left. |
| Models | `claude-sonnet` (Sonnet 5, default), `claude-opus` (Opus 4.8, available from Day 1), `claude-haiku` |

You do **not** need AWS credentials for Days 1–3. The gateway holds those.
Day 4 adds two Python packages (`claude-agent-sdk`, `langgraph`); see day4-orchestration-evals-cicd/README.md.

---

## Layout

| Directory | What it is |
|---|---|
| `labkit/` | Shared plumbing: gateway client, tracing, cost, budget. **Not** an agent framework — the loop is yours to write. |
| `day1-foundations/` | Agent fundamentals. Build a tool-using agent from scratch. |
| `day2-agentic-coding/` | Claude Code on real code: greenfield, brownfield, grounding. |
| `bootstrap/` | `doctor` and the gateway conformance test. |

**Reference implementations** are published after each lab, under
`dayN-*/labN.M-*/reference/`. Read them once your own version works — the point
of the lab is writing it.

Days 3 and 4 — MCP, agent integration, security, multi-agent, evals, CI/CD,
AgentCore and the capstone — are added to this repository before those sessions.
`git pull` on the morning of Day 3.
| `traces/` | Every agent run you make lands here as JSON lines. Gitignored. |

---

## Commands

```bash
make doctor      # is my machine ready?
make test        # offline checks for every lab
make lab1        # run Lab 1.1
make cost        # how much of today's budget have I used?
```

Live checks call the model and cost about a cent. They are opt-in:

```bash
LAB_LIVE=1 make test
```

Java labs build through the root POM, so always pass `-am`:

```bash
mvn -q test -pl day1-foundations/lab1-bare-metal-loop/java -am
```

---

## Rules that matter

1. **Never commit `.env`, your key, or any credential.** A commit hook blocks
   the obvious cases, but it is not a substitute for care.
2. **Your key is yours.** Spend is attributed to you by name.
3. **Every lab has a step limit and a budget ceiling.** They are deliberately
   low. An agent without them is a production incident waiting to happen —
   that is Day 1's first lesson, and the guardrails stay on all four days.
4. **Solutions are on the `solutions` branch.** Try each TODO before you look.

---

## If something breaks

1. `make doctor`
2. [setup/06-troubleshooting.md](setup/06-troubleshooting.md)
3. Ask. Everyone else is probably hitting it too.
