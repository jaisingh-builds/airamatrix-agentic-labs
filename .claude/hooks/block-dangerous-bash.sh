#!/usr/bin/env bash
# Refuse shell commands that destroy work or rewrite shared history.
# PreToolUse on Bash. Exit 2 blocks; the reason goes back to the model.
#
# Scope, deliberately narrow: the handful of commands that are unrecoverable in
# a training room on a deadline. It is NOT a sandbox - the permission system is
# the control, this is the seatbelt. It FAILS OPEN if it cannot read its input.
#
# Matching is done in Python, not shell globs. The glob version blocked
# `rm -rf /Users/me/proj/target` (every absolute path contains "rm -rf /") and
# even `grep -rn "rm -rf /"`, which is what the grounding demo asks people to
# run. A guardrail that fires on ordinary work gets switched off in a week.
set -uo pipefail
payload=$(cat)
PAYLOAD="$payload" python3 <<'PY'
import json, re, sys, os

try:
    cmd = json.loads(os.environ["PAYLOAD"]).get("tool_input", {}).get("command", "") or ""
except Exception:
    sys.exit(0)                       # fail open - unreadable payload
if not cmd.strip():
    sys.exit(0)

# Ignore anything inside quotes: writing ABOUT a command is not running it.
stripped = re.sub(r"""'[^']*'|"[^"]*\"""", " ", cmd)
c = re.sub(r"\s+", " ", stripped)

home = re.escape(os.path.expanduser("~"))
RULES = [
    (rf"\brm\s+-[a-z]*(?:[rR][a-z]*f|f[a-z]*[rR])[a-z]*\s+(/|{home}/?|\$HOME/?|~/?)(\s|$)",
     "that would delete a home or root directory.",
     "Name a path inside the repository instead."),
    (r"\bgit\s+push\b(?!.*--force-with-lease).*(--force\b|\s-f\b)",
     "force-push rewrites history other people have pulled.",
     "Use --force-with-lease, and only on a branch that is yours."),
    (r"\bgit\s+reset\s+--hard\b",
     "reset --hard discards uncommitted work irreversibly.",
     "git stash keeps it. If you truly want it gone, run it yourself."),
    (r"\bgit\s+clean\b[^|;]*-[a-z]*f[a-z]*d|\bgit\s+clean\b[^|;]*-[a-z]*d[a-z]*f",
     "git clean -fd deletes untracked files, including your .env.",
     "Run 'git clean -nd' first and read what it lists."),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|d)?sh\b",
     "piping a download straight into a shell runs unreviewed code.",
     "Download it, read it, then run it."),
    (r"\bchmod\b[^|;]*\b777\b",
     "chmod 777 makes a file world-writable.",
     "Use 600 for anything holding a credential."),
]
for pat, why, hint in RULES:
    if re.search(pat, c):
        print(f"Blocked: {why}", file=sys.stderr)
        print(hint, file=sys.stderr)
        sys.exit(2)
sys.exit(0)
PY
