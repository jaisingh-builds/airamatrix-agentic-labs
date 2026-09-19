#!/usr/bin/env bash
# Refuse shell commands that destroy work or rewrite shared history.
# PreToolUse on Bash. Exit 2 blocks and the reason goes back to the model.
#
# Scope, deliberately narrow. This blocks the handful of commands that are
# unrecoverable in a training room on a deadline. It is not a sandbox: anything
# determined gets through, and that is fine - the control is the permission
# system, this is the seatbelt.
set -uo pipefail
cmd=$(cat | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command",""))
except Exception: pass') || exit 0
[ -n "$cmd" ] || exit 0

deny () { echo "Blocked: $1" >&2; echo "$2" >&2; exit 2; }

case "$cmd" in
  *"rm -rf /"*|*"rm -rf ~"*|*"rm -rf \$HOME"*)
      deny "that would delete a home or root directory." \
           "If you meant a path inside the repo, write it out in full." ;;
  *"git push"*--force-with-lease*)
      : ;;                      # the safe form - it refuses if the remote moved
  *"git push"*--force*|*"git push"*" -f "*)
      deny "force-push rewrites history other people have pulled." \
           "Use --force-with-lease, and only on a branch that is yours." ;;
  *"git reset --hard"*)
      deny "reset --hard discards uncommitted work irreversibly." \
           "git stash keeps it. If you truly want it gone, run it yourself." ;;
  *"git clean -"*f*d*|*"git clean -"*d*f*)
      deny "git clean -fd deletes untracked files, including your .env." \
           "Run 'git clean -nd' first and read what it lists." ;;
  *curl*"| sh"*|*curl*"| bash"*|*wget*"| sh"*|*wget*"| bash"*)
      deny "piping a download straight into a shell runs unreviewed code." \
           "Download it, read it, then run it." ;;
  *"chmod 777"*)
      deny "chmod 777 makes a file world-writable." \
           "Use 600 for anything holding a credential." ;;
esac
exit 0
