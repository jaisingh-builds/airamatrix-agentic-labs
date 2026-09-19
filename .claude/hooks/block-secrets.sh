#!/usr/bin/env bash
# Refuse edits that would write a credential INTO the repository.
# Wired into .claude/settings.json as a PreToolUse hook on Write|Edit.
# Day 3 teaches secrets hygiene; this enforces it from Day 1.
#
# SCOPE, deliberately narrow - this is teaching material, not the control:
#   - fires on Write and Edit only, so a secret written by a shell command
#     never reaches it;
#   - matches the two ACCESS KEY prefixes (AKIA long-term, ASIA temporary)
#     and two token shapes, plus a KEYED aws_secret_access_key assignment -
#     the bare 40-char secret is not matched on its own because base64 of that
#     length is far too common to block safely. AIDA/AROA are IAM
#     user and role IDs, not credentials - matching them only adds false
#     positives on policy documents;
#   - scans the PROPOSED CONTENT only. Scanning the whole payload would block
#     you from REMOVING a credential, because an Edit payload carries the old
#     text too. That bug is easy to write and hard to notice.
# The repository-wide control belongs in CI, with a maintained scanner.
set -uo pipefail

payload=$(cat)

proposed=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(3)
t = d.get("tool_name", "")
i = d.get("tool_input", {}) or {}
parts = []
if t == "Write":
    parts.append(i.get("content", ""))
elif t == "Edit":
    parts.append(i.get("new_string", ""))
elif t == "MultiEdit":
    parts += [e.get("new_string", "") for e in (i.get("edits") or [])]
elif t == "NotebookEdit":
    parts.append(i.get("new_source", ""))
else:
    parts.append(i.get("content", "") or i.get("new_string", ""))
sys.stdout.write("\n".join(p for p in parts if isinstance(p, str)))
') || {
  echo "block-secrets: could not parse the hook payload; allowing the edit." >&2
  echo "The CI secret scan is the control. Tell the trainer this fired." >&2
  exit 0
}

if printf '%s' "$proposed" | grep -qE \
   '(sk-(ant|aira)-?[A-Za-z0-9_-]{20,}|(AKIA|ASIA)[A-Z0-9]{16}|aws_secret_access_key[[:space:]]*[=:][[:space:]]*[^[:alnum:]]?[A-Za-z0-9/+=]{32,})'; then
  echo "Blocked: that edit would write something shaped like a credential." >&2
  echo "Keys belong in .env or ~/.claude/settings.json, never in the repo." >&2
  exit 2
fi
exit 0
