#!/bin/sh
# Lab 4.4 fix: start server.ts with the token read from a file OUTSIDE the repo,
# so the token is never in the environment Claude Code runs in - and therefore
# never in the environment its Bash tool inherits (`env`, `printenv`, `echo $...`).
#
#   mkdir -p ~/.config/aira-ops && umask 077 && printf %s "$AIRA_OPS_TOKEN" > ~/.config/aira-ops/token
#   unset AIRA_OPS_TOKEN            # then start claude from this shell
#
# .mcp.json:  "command": "./run-server.sh", "args": []   (and no "env" token entry)
TOKEN_FILE="${AIRA_OPS_TOKEN_FILE:-$HOME/.config/aira-ops/token}"
if [ ! -r "$TOKEN_FILE" ]; then
  echo "aira-ops: no readable token file at $TOKEN_FILE" >&2
  exit 1
fi
AIRA_OPS_TOKEN="$(cat "$TOKEN_FILE")" exec node --experimental-strip-types --no-warnings "$(dirname "$0")/server.ts"
