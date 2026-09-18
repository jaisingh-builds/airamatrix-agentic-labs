#!/usr/bin/env bash
# Refuse edits that would write a credential into the repository.
# Wired into .claude/settings.json as a PreToolUse hook on Write/Edit.
# Day 3 teaches secrets hygiene; this enforces it from Day 1.
#
# SCOPE, deliberately narrow - this is teaching material, not the control:
#   - fires on Write and Edit only, so a secret written by a shell command
#     never reaches it;
#   - matches the two ACCESS KEY prefixes (AKIA long-term, ASIA temporary)
#     and two token shapes, not every credential format. AIDA/AROA are IAM
#     user and role IDs, not credentials - matching them only adds false
#     positives on policy documents.
# The repository-wide check belongs in CI, with a maintained scanner.
set -uo pipefail
payload=$(cat)
if printf '%s' "$payload" | grep -qE '(sk-(ant|aira)-?[A-Za-z0-9_-]{20,}|(AKIA|ASIA)[A-Z0-9]{16})'; then
  echo "Blocked: that edit contains something shaped like a credential." >&2
  echo "Keys belong in .env or ~/.claude/settings.json, never in the repo." >&2
  exit 2
fi
exit 0
