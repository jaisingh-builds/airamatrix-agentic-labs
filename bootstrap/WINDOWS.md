# Day 3 on Windows

Everything on Day 3 runs on Windows. The bash commands on the slides map to one
PowerShell script, `bootstrap\day3.ps1`, which works in Windows PowerShell 5.1 and PowerShell 7.

## Once: install

| Tool | Version | Get it |
|---|---|---|
| Git for Windows | any recent | git-scm.com (Claude Code also uses its Git Bash) |
| Python | 3.10+ (3.12 recommended) | python.org. **Tick "Add python.exe to PATH".** |
| Node.js | 22 LTS (22.18+) or newer | nodejs.org |
| Claude Code | latest | `npm install -g @anthropic-ai/claude-code` |

Gateway settings live in `%USERPROFILE%\.claude\settings.json` (from Day 1). The script reads them from there.

## Every time: from the repo root

```powershell
cd airamatrix-agentic-labs
git pull
powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 setup    # checks, token, all offline tests
```

**Then close and reopen your terminals and VS Code.** `setup` stores `AIRA_OPS_TOKEN` as a user
environment variable, so every window and Claude Code use the same token. Windows that were
already open don't see it.

```powershell
powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 start    # aira-ops opens in its own window
powershell -ExecutionPolicy Bypass -File bootstrap\day3.ps1 status   # up? does it accept my token?
```

| Slide / lab | macOS / Linux | Windows |
|---|---|---|
| 6: start aira-ops | `python3 aira-ops/aira_ops.py --reset` | `bootstrap\day3.ps1 start` |
| 6: offline suites | `make day3-test` | `bootstrap\day3.ps1 test` |
| 4.1: tool clinic | `python3 clinic.py --tools bad mine` | `bootstrap\day3.ps1 clinic` (or `-Tools bad`) |
| 4.2: second client | `python3 client.py` | `bootstrap\day3.ps1 mcp` |
| 4.2: Claude Code | `claude` in `lab4-2-mcp-server`, then `/mcp` | same (`.mcp.json` works as is) |
| 4.3: starter service | `python3 starter/service.py` | `bootstrap\day3.ps1 service` |
| 4.3: try to break it | `RUN_TIMEOUT_S=5 python3 ...` | `$env:RUN_TIMEOUT_S=5; bootstrap\day3.ps1 service` |
| stop everything | `lsof -ti :8160 \| xargs kill` | `bootstrap\day3.ps1 stop` |

To run the other lab commands by hand in PowerShell, use `python` (or `py -3`) instead of
`python3`, and `$env:NAME="value"` instead of `NAME=value`. For the slide's `curl` demos, use
**Git Bash**: it runs the bash lines from the slides unchanged.

## Known differences

- **Lab 4.4 Part A, step 2 (the hardened run)** uses `run-server.sh` and Claude Code's OS sandbox,
  which isn't available on native Windows. Do it in WSL, or pair with someone on macOS or Linux.
  Every other Lab 4.4 step works.
- `make` isn't installed on Windows by default. The script replaces `make day3-test` and `make doctor`
  is Day 1 only. `make lab4-sabotage` = `python day3-integration-security\lab4-untrusted-web\tools\sabotage.py`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `running scripts is disabled on this system` | Use the `powershell -ExecutionPolicy Bypass -File ...` form shown above |
| `Python 3.10+ not found`, or `python` opens the Microsoft Store | Install from python.org with "Add to PATH"; turn off the `python.exe` App Execution Alias in Settings |
| Every call returns 401 | An old aira-ops holds port 8150 with another token. `status` tells you; `start` replaces it |
| `/mcp` in Claude Code shows `failed` | Claude Code was started before `setup`. Reopen VS Code or the terminal |
| `UnicodeEncodeError` / garbled output | Run through the script (it sets `PYTHONUTF8=1`) or set `$env:PYTHONUTF8=1` |
| Tests fail after cloning on Windows | Line endings. `setup` renormalises a clean checkout to LF automatically |

## Day 4

```powershell
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 setup     # venv + claude-agent-sdk + langgraph + offline tests
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 test      # = make day4-test
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 starters  # your TODO progress
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 tokens    # Lab 5.1 tokens (user env vars, never files)
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 evals     # Lab 5.2 golden set (~$0.70)
powershell -ExecutionPolicy Bypass -File bootstrap\day4.ps1 review -Base main -Head my-branch   # Lab 5.3
```

Node 22.6+ is required (the MCP server runs `server.ts` directly). Lab 5.3 finds
Claude Code through its `claude.cmd` shim.
