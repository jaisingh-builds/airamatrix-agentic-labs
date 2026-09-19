#!/usr/bin/env bash
# Is this machine ready for the labs?
# Run this first, every morning. It checks, it does not change anything.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PASS=0; FAIL=0; WARN=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n     -> %s\n' "$1" "$2"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n     -> %s\n' "$1" "$2"; WARN=$((WARN+1)); }
hdr()  { printf '\n%s\n' "$1"; }

echo "AiraMatrix lab environment check"
echo "================================"

# ------------------------------------------------------------------ runtimes
hdr "Runtimes"
if command -v java >/dev/null 2>&1; then
  V=$(java -version 2>&1 | head -1 | grep -oE '[0-9]+' | head -1)
  [ "${V:-0}" -ge 21 ] && ok "Java $V" || bad "Java $V is too old" "install JDK 21 - see setup/01-prerequisites.md"
else bad "Java not found" "install JDK 21 - see setup/01-prerequisites.md"; fi

if command -v node >/dev/null 2>&1; then
  V=$(node --version | tr -d 'v' | cut -d. -f1)
  [ "${V:-0}" -ge 20 ] && ok "Node $(node --version)" || bad "Node $(node --version) is too old" "install Node 20+"
else bad "Node not found" "install Node 20 or newer"; fi

if command -v python3 >/dev/null 2>&1; then
  V=$(python3 -c 'import sys;print(sys.version_info[1])')
  [ "${V:-0}" -ge 10 ] && ok "Python 3.$V" || bad "Python 3.$V is too old" "install Python 3.10+"
else bad "Python 3 not found" "install Python 3.12"; fi

command -v git   >/dev/null 2>&1 && ok "git"   || bad "git not found" "install git"
command -v mvn   >/dev/null 2>&1 && ok "maven" || warn "maven not found" "needed for the Java labs only"
command -v claude >/dev/null 2>&1 && ok "Claude Code $(claude --version 2>/dev/null | head -1)" \
  || warn "Claude Code not found" "needed from Day 2 - see setup/03-claude-code.md"

# ----------------------------------------------------------------- lab config
hdr "Lab configuration"
SHELL_BASE_URL="${ANTHROPIC_BASE_URL:-}"
if [ -f .env ]; then
  ok ".env exists"
  set -a; . ./.env 2>/dev/null; set +a
  case "${ANTHROPIC_AUTH_TOKEN:-}" in
    sk-PASTE*|"") bad "no key in .env" "paste the key from your access card into ANTHROPIC_AUTH_TOKEN" ;;
    sk-*)         ok "key present (${ANTHROPIC_AUTH_TOKEN:0:10}...)" ;;
    *)            warn "key looks unusual" "keys start with sk-" ;;
  esac
  case "${ANTHROPIC_BASE_URL:-}" in
    "")                 bad "ANTHROPIC_BASE_URL not set" "paste the gateway URL from your access card" ;;
    *GATEWAY-HOST*)     bad "gateway URL is still the placeholder" \
                            "your access card has the real host - paste it into ANTHROPIC_BASE_URL" ;;
    https://*)          ok "gateway URL: $ANTHROPIC_BASE_URL" ;;
    *)                  bad "gateway URL looks wrong: ${ANTHROPIC_BASE_URL}" "it should start with https://" ;;
  esac

  # An exported variable BEATS .env. A stale export from another install is a
  # confusing way to fail, so say so plainly.
  if [ -n "$SHELL_BASE_URL" ] && [ "$SHELL_BASE_URL" != "${ANTHROPIC_BASE_URL:-}" ]; then
    warn "an exported ANTHROPIC_BASE_URL overrides .env in the lab code" \
         "shell has '$SHELL_BASE_URL', .env has '${ANTHROPIC_BASE_URL}' - run: unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN"
  fi
else
  bad ".env missing" "run: cp .env.example .env   then paste your key"
fi

# -------------------------------------------------------------------- gateway
hdr "Gateway"
if [ -n "${ANTHROPIC_BASE_URL:-}" ] && [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] \
   && [ "${ANTHROPIC_AUTH_TOKEN}" != "sk-PASTE-YOUR-KEY-HERE" ]; then
  CODE=$(curl -s -m 20 -o /tmp/doctor_resp -w '%{http_code}' -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
    -H "authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -H "anthropic-version: 2023-06-01" \
    -H 'content-type: application/json' \
    -d '{"model":"claude-haiku","max_tokens":4,"messages":[{"role":"user","content":"ok"}]}' 2>/dev/null || echo 000)
  case "$CODE" in
    200) ok "model call succeeded" ;;
    401|403) bad "key rejected ($CODE)" "check you copied the whole key; ask the trainer to reissue" ;;
    429) warn "rate limited or out of budget (429)" "your daily budget may be spent - check with 'make cost'" ;;
    000) bad "cannot reach the gateway" "check Wi-Fi, and that $ANTHROPIC_BASE_URL is allowed through the proxy" ;;
    *)   bad "unexpected response $CODE" "$(head -c 160 /tmp/doctor_resp 2>/dev/null)" ;;
  esac
  rm -f /tmp/doctor_resp
else
  warn "skipped - configure .env first" "cp .env.example .env"
fi

# --------------------------------------------------------------------- egress
hdr "Network egress"
for HOST_URL in https://registry.npmjs.org https://repo.maven.apache.org https://api.anthropic.com; do
  curl -s -m 12 -o /dev/null "$HOST_URL" 2>/dev/null \
    && ok "reachable: $HOST_URL" \
    || warn "cannot reach $HOST_URL" "ask IT to allow-list it - see setup/ALLOWLIST.md"
done

printf '\n────────────────────────────────\n'
printf '  PASS %d   WARN %d   FAIL %d\n' "$PASS" "$WARN" "$FAIL"
printf '────────────────────────────────\n'
if [ "$FAIL" -gt 0 ]; then
  echo "Fix the FAIL lines above, then run this again. Ask the trainer if you are stuck."
  exit 1
fi
echo "Ready. Start with day1-foundations/lab1-bare-metal-loop/README.md"
