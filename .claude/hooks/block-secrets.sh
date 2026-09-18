#!/usr/bin/env bash
# Refuse edits that would write a credential into the repository.
# Wired into .claude/settings.json as a PreToolUse hook on Write/Edit.
# Day 3 teaches secrets hygiene; this enforces it from Day 1.
set -uo pipefail
payload=$(cat)
if printf '%s' "$payload" | grep -qE '(sk-(ant|aira)-?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})'; then
  echo "Blocked: that edit contains something shaped like a credential." >&2
  echo "Keys belong in .env or ~/.claude/settings.json, never in the repo." >&2
  exit 2
fi
exit 0
