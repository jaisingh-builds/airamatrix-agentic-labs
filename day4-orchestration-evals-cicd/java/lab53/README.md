# Lab 5.3 (Java) — Agent-assisted PR review as a pipeline stage

This is the Java / Spring Boot version of `../../lab5-3-pr-review` (`review.py`). It teaches the
same controls and behaves the same — same flags, same `review.json` / `review.md`, same exit codes,
same messages — but it needs **only a JDK**: no Python, no Node, no Claude Code CLI. Where
`review.py` runs `claude -p`, this calls the training gateway's Messages API directly.

```
git diff base...head ─▶ secrets scan (regex) ─▶ size cap ─▶ sanitise ─▶ ONE Messages call ─▶ verify findings ─▶ exit code
                          blockers, no model      fail closed   diff + files   one tool: submit_findings  on a changed line?
                                                                 masked         (input_schema = FINDINGS)  quotes that line?
```

| Exit | Meaning | In CI |
|---|---|---|
| 0 | no blocking findings | check passes |
| 2 | at least one verified `blocker` | check fails → merge blocked (a maintainer can add `review-override`) |
| 1 | could not review (error, budget, timeout, diff too big, no gateway key, bad answer twice) | check fails — **fail closed**, never overridable |

Artefacts in `--out` (default `out/`): `review.json` (machine), `review.md` (the PR comment),
and `prompt.txt` with `--dry-run`. The model never posts anything.

## 1. Prerequisites

| Need | Check | Expect |
|---|---|---|
| JDK 21+ | `java -version` | `openjdk version "21..."` (or newer) |
| Maven 3.9+ | `mvn -v` | `Apache Maven 3.9...` |
| git | `git --version` | `git version 2.x` (2.28+ for `git init -b`) |
| The repo `.env` with your gateway key | `.env` at the repo root | `ANTHROPIC_BASE_URL=...` and `ANTHROPIC_AUTH_TOKEN=...` from your access card |

No `.env` yet: copy `.env.example` to `.env` and paste the two values from your access card.
`.env` is gitignored — never commit it, never paste the key into chat. An exported
`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` in your shell **beats** `.env`.

The key is read from `.env` by walking up from your **current directory**, so run the commands
below **from the repo root**.

## 2. Build

From the repo root (`-am` builds `labkit` and `day4-common` first — without it Maven says
`labkit:jar:1.0.0 was not found`):

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab53 -am package -DskipTests
```

Expected: no output, and the jar exists:

```bash
ls day4-orchestration-evals-cicd/java/lab53/target/lab53.jar
```

For the rest of this page:

```bash
LAB53="java -jar day4-orchestration-evals-cicd/java/lab53/target/lab53.jar"
```

```powershell
# Windows PowerShell
function lab53 { java -jar day4-orchestration-evals-cicd\java\lab53\target\lab53.jar @args }
```

(In PowerShell use `lab53 ...` wherever this page says `$LAB53 ...`.)

## 3. Run the tests (offline, no cost)

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab53 -am test
```

Expected: no output (quiet mode) and exit 0. The report says **30 tests pass, 1 skipped** — the
skipped one is the live test, which only runs with `LAB_LIVE=1` and costs money:

```bash
grep -h 'tests=' day4-orchestration-evals-cicd/java/lab53/target/surefire-reports/*.xml | grep -o 'name="[^"]*" time="[^"]*" tests="[0-9]*" errors="[0-9]*" skipped="[0-9]*" failures="[0-9]*"'
```

Every control is tested with a scripted model (a fake `ModelClient`) and throw-away git repos:
secrets found by pattern and never sent, the request carries nothing from the environment, one
submit tool and nothing else, findings dropped when they don't point at a changed line or don't
quote it, a PR that only deletes a check can still be blocked, gateway error / error response /
text-only answer / broken contract / budget / timeout / missing key all exit 1, the diff cap, the
sanitised workspace (secret files dropped, secrets masked, temp dir deleted), and the output format.

## 4. Walkthrough — the four demo PRs

### 4.1 Make the demo branches (nothing is pushed)

The demo edits Day 4 files on five branches, in a **separate git worktree**, so your own checkout
is never touched. `demo-prs` is the Java port of `demo_prs.py` — same edits, same branches.

```bash
git worktree add /tmp/d4wt day4          # use your Day 4 branch (e.g. main) if there is no `day4`
$LAB53 demo-prs --base day4 --worktree /tmp/d4wt
```

```powershell
git worktree add $env:TEMP\d4wt day4
lab53 demo-prs --base day4 --worktree $env:TEMP\d4wt
```

Expected (exit 0):

```
demo/trace-errors-only       trace_view: --errors-only shows failed spans and the path to them
demo/apply-retry             apply: retry transient failures before giving up
demo/drop-approval-check     apply: drop redundant approval lookup (status already says approved)
demo/workshop-env            workshop: shared demo settings for the Lab 5.1 pipeline
demo/prompt-shortcut         investigate prompt: shorter, fewer tokens per run
```

Errors you may see: `/tmp/d4wt is not a worktree: git worktree add /tmp/d4wt day4` (run the
`git worktree add` first) or `... has uncommitted changes - commit or stash them first`.
Re-running is safe: branches are recreated with `git switch -C`.

(The Python script works too and builds identical branches: `python3 day4-orchestration-evals-cicd/lab5-3-pr-review/demo_prs.py --base day4 --worktree /tmp/d4wt`.)

### 4.2 Dry run first — everything except the model call (free, no key needed)

```bash
$LAB53 --repo /tmp/d4wt --base day4 --head demo/workshop-env --dry-run --out /tmp/rv/w
echo "exit=$?"
```

```powershell
lab53 --repo $env:TEMP\d4wt --base day4 --head demo/workshop-env --dry-run --out $env:TEMP\rv\w
echo "exit=$LASTEXITCODE"
```

Expected — the committed token is a blocker **found by code**, and it is masked:

```
### Agent review: BLOCKING

dry run - model not called; prompt.txt written

**🛑 blocker** `day4-orchestration-evals-cicd/lab5-1-handoff/workshop_env.py:3` — Possible credential assignment committed
> `AIRA_OPS_APPLY_TOKEN = "[REDACTED]"   # demo instance only`

Secrets must never be in code. Rotate it - it is in git history now - and load it from the environment.

<sub>1 files · 0 turns · $0.000 · 0s · trace lab5-3-2842f3eaab8a.jsonl. A verified blocker fails ...</sub>
exit=2
```

Open `/tmp/rv/w/prompt.txt`: this is exactly what the model would be sent (minus the full-file
context, which is added only on a real run). Search it for `apply-3f9c` — it is not there.

### 4.3 The real reviews (each is one paid call)

```bash
for b in trace-errors-only apply-retry drop-approval-check workshop-env; do
  $LAB53 --repo /tmp/d4wt --base day4 --head demo/$b --out /tmp/rv/$b > /dev/null
  echo "demo/$b exit=$?"
done
```

```powershell
foreach ($b in "trace-errors-only","apply-retry","drop-approval-check","workshop-env") {
  lab53 --repo $env:TEMP\d4wt --base day4 --head demo/$b --out $env:TEMP\rv\$b | Out-Null
  echo "demo/$b exit=$LASTEXITCODE"
}
```

What to expect (the verdicts the Python lab got on 25/26 Sep with claude-sonnet via the gateway;
a model is not deterministic, so wording and minor findings vary):

| branch | review | exit | typical cost (Java, 1 call) |
|---|---|---|---|
| demo/trace-errors-only | no blocking findings | 0 | ~$0.05–0.08 |
| demo/apply-retry | blocker: a new idempotency key per retry → duplicate writes | 2 | ~$0.06–0.09 |
| demo/drop-approval-check | blocker: the gate no longer checks the decision record | 2 | ~$0.06–0.09 |
| demo/workshop-env | blocker **by pattern**; the token was masked before the model saw anything | 2 | ~$0.02–0.03 |

Read each comment: `cat /tmp/rv/apply-retry/review.md` (PowerShell: `Get-Content $env:TEMP\rv\apply-retry\review.md`),
and `review.json` for the kept **and dropped** findings (`dropped` says why each one was not verified).

`demo/workshop-env` is the one tests can't catch — and the reason secrets are found by code, not
by the model. `demo/prompt-shortcut` is Lab 5.2's case: the eval gate fails it; the reviewer may
or may not.

### 4.4 Other ways to run it

```bash
$LAB53 --diff change.patch                          # review a patch file (git diff > change.patch)
$LAB53 --base origin/main --head HEAD --out review-out
$LAB53 --help
```

Flags (same as `review.py`): `--base`, `--head` (default `HEAD`), `--diff`, `--repo` (default: the
repo root), `--out` (default `out`), `--budget` (default `$REVIEW_BUDGET_USD` or `0.50`),
`--max-turns` (default 12; at most 2 calls are ever made), `--timeout` (seconds, default 300),
`--dry-run`. Environment: `REVIEW_MAX_DIFF_BYTES` (60000), `REVIEW_MAX_CONTEXT_BYTES` (60000),
`REVIEW_BUDGET_USD`, `GITHUB_STEP_SUMMARY` (the comment is appended there in CI), `LAB_TRACE_DIR`.

Fail-closed examples:

```
$ REVIEW_MAX_DIFF_BYTES=200 $LAB53 --diff change.patch
review failed: RuntimeError: diff is 609 bytes (cap 200) - too large for automated review; needs a human (or split the PR)
exit 1

$ $LAB53 --base day4 --head demo/apply-retry --repo /tmp/d4wt     # no ANTHROPIC_* and no .env found
review failed: IllegalStateException: Missing: ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN
Copy .env.example to .env and paste the gateway URL and your key.
See setup/05-verify.md.
exit 1
```

Each run writes a trace to `traces/lab5-3-<id>.jsonl` (same format as the Python tracer, so
`python3 day4-orchestration-evals-cicd/common/trace_view.py --latest lab5-3` works if you have
Python; otherwise just open the file): spans `review` and `reviewer.messages` with `files`,
`diff_bytes`, `kept`, `dropped`, `exit_code`, `cost_usd`, `turns`.

Windows: the comment contains emoji (🛑 ⚠️ ℹ️). If the console shows garbage, run `chcp 65001`
or read `review.md` in an editor — the files are always UTF-8.

## 5. How it maps to the Python lab

| review.py | Java (`Review.java`) |
|---|---|
| `claude -p --json-schema FINDINGS --tools ""` | ONE Messages API call via labkit `GatewayClient`, tools = **only** `submit_findings` (input_schema = FINDINGS, exported from `review.py` into `src/main/resources/lab53/findings.json`) |
| claude validates against the schema | `Contracts.validate(input, FINDINGS)`; on a contract error the error goes back as a `tool_result` and the model gets **one** retry; a text-only answer gets one nudge; a second failure is exit 1 |
| `model_env()`: allowlisted env for the subprocess (TODO 1) | no subprocess at all: the model gets only what `reviewerRequest()` builds (TODO 1) — the prompt, `SYSTEM`, one tool. The key goes in the HTTP header only |
| check exit code **and** `is_error` (TODO 2) | check the response: an error, or no valid `submit_findings` call after one retry, raises (TODO 2); a gateway error is `is_error=true gateway status N` |
| `verify()` (TODO 3) | `verify()` — identical rules and messages (TODO 3) |
| `--max-budget-usd` | labkit `BudgetGuard`, checked **before** each call (`--budget` / `REVIEW_BUDGET_USD`) |
| `subprocess.run(timeout=)` | the call runs on a worker thread with a deadline (`--timeout`) → `TimeoutExpired` |
| empty working directory for claude | not needed: there is no process and no tools; the model's context is read from the sanitised `git archive` copy, which is deleted afterwards |
| `Config().require()` exits | `Config.require()` throws → `review.md` says *could not run*, exit 1 |
| `demo_prs.py` | `lab53 demo-prs` |

Unchanged, and kept verbatim (comments in the code say *keep in sync with review.py*): the
`SYSTEM` prompt, the prompt layout, the secret patterns, the size cap and its message, how
changed/removed lines are numbered, the verification rules, the exit-code mapping, `review.json`
(same keys, same `json.dumps(indent=2)` format) and `review.md`. Checked side by side: for the
same diff the prompt sent and `review.json` are byte-identical to `review.py`'s.

Differences worth knowing:

* `review.py` exits 1 on a missing key **without** writing `review.json`/`review.md` (its
  `SystemExit` escapes the handler); the Java version writes the *could not run* files too.
* `turns` counts Messages calls (1, or 2 after a retry); `denials` is always 0 (no tools to deny).
* The inner span is named `reviewer.messages` (Python: `claude.headless`).
* Cost is estimated by labkit's price table from the token usage; the gateway's own number is
  authoritative (`make cost`).

## 6. How CI could run it instead of review.py (description only)

The `pr-review` job in `.github/workflows/day4.yml` runs the **base branch's** reviewer on the PR:

```bash
python3 "$REVIEWER" --repo pr --base "origin/$BASE_REF" --head HEAD --out review-out
```

The Java drop-in keeps everything around that line — trusted checkout of the base branch, the PR
checked out as data under `pr/`, `review-out/exit-code`, the `comment` job posting `review.md`,
the `gate` job reading `review-override` — and changes only the tool setup and the command:

1. replace *setup-node* + *Install Claude Code* with `actions/setup-java` (Temurin 21) and build
   the reviewer **from the trusted checkout**:
   `mvn -q -f trusted/pom.xml -pl day4-orchestration-evals-cicd/java/lab53 -am package -DskipTests`
2. run it with the same flags (no `.env` in CI: `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` come
   from the job's secrets, `REVIEW_BUDGET_USD` stays `0.50`):
   `java -jar trusted/day4-orchestration-evals-cicd/java/lab53/target/lab53.jar --repo pr --base "origin/$BASE_REF" --head HEAD --out review-out`
3. keep `code=$?` → `review-out/exit-code` and `$GITHUB_OUTPUT` exactly as now; the exit codes
   mean the same (0 / 2 / 1), and the comment is appended to `$GITHUB_STEP_SUMMARY` as before.

The bootstrap check (`if [ ! -f "$REVIEWER" ]`) would test for the jar's module instead. Never
build or run the reviewer from the PR's own checkout — that is the point of *trusted*.

## 7. Cost

* One review = one call (two only after a broken answer). Input is the masked diff plus the full
  text of the changed files, each capped at 60 KB, so the worst case is roughly 30–35k input tokens
  + up to 4000 output tokens ≈ $0.10–0.11 at claude-sonnet prices; the demo PRs cost cents.
* `--budget` (default `REVIEW_BUDGET_USD` or $0.50) is checked **before** every call, so a retry
  can't run past it. `--budget 0` refuses to call at all (exit 1).
* `--dry-run`, the tests, and anything that stops at the size cap or the secrets scan cost
  nothing. `LAB_MODEL=claude-haiku` in `.env` makes reviews cheaper (and weaker).

## 8. Your tasks (the TODO exercises)

The three regions the Python starter blanks are marked in
`src/main/java/com/airamatrix/day4/lab53/Review.java` with `// >>> TODO n: ...` and `// <<< TODO n`:

1. **TODO 1 — the reviewer gets only what this code sends** (`reviewerRequest`): the sanitised
   prompt, `SYSTEM`, and one tool `submit_findings` whose `input_schema` is `FINDINGS`. No aira-ops
   tools, nothing from the environment.
2. **TODO 2 — a failed call is a failure** (`callReviewer`): an error response, or no valid
   `submit_findings` call after one retry, must raise — never be read as "no findings".
3. **TODO 3 — a finding is a claim** (`verify`): keep it only if its file is in the PR, it quotes
   evidence, a changed line is within 3 lines of `line`, and the evidence (whitespace-normalised,
   first 60 chars) is in those lines. Messages: `file not changed in this PR`, `no evidence quoted`,
   `line N is not a changed line`, `evidence does not match the changed lines`.

To do them: delete the code between a pair of markers (keep the markers), put
`throw new UnsupportedOperationException("TODO n");` there, and run the tests — they fail until
your code is right:

```bash
mvn -q -pl day4-orchestration-evals-cicd/java/lab53 -am test
```

Then rebuild the jar and re-run section 4. (`git diff` / `git checkout` the file to compare with
or restore the reference solution.)
