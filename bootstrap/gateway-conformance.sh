#!/usr/bin/env bash
# Gateway conformance test — AiraMatrix Agentic AI & Agentic Coding
#
# Claude Code talking to Bedrock through an LLM gateway has six documented ways
# to fail. Checks 3 and 4 fail SILENTLY — the only symptom of a broken #3 is the
# bill. Run this before every training day.
#
# Usage: ./gateway-conformance.sh [base_url] [api_key]
set -uo pipefail

BASE="${1:-${ANTHROPIC_BASE_URL:-http://localhost:4000}}"
KEY="${2:-${ANTHROPIC_AUTH_TOKEN:-}}"
MODEL="${CONFORMANCE_MODEL:-claude-sonnet}"
PASS=0; FAIL=0; WARN=0
H_AUTH="Authorization: Bearer $KEY"
H_VER="anthropic-version: 2023-06-01"
H_JSON="Content-Type: application/json"

ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN  $1"; WARN=$((WARN+1)); }
hdr()  { echo; echo "[$1] $2"; }

[ -z "$KEY" ] && { echo "No API key. Pass as \$2 or set ANTHROPIC_AUTH_TOKEN."; exit 2; }
echo "Gateway conformance — $BASE (model: $MODEL)"

# ---------------------------------------------------------------- 1. reachable
hdr 1 "Endpoint reachable and credential valid"
R1=$(curl -s -w '\n%{http_code}' -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"say OK\"}]}")
C1=$(echo "$R1" | tail -1)
[ "$C1" = "200" ] && ok "/v1/messages returned 200" || bad "/v1/messages returned $C1 — $(echo "$R1"|head -c 200)"

# ------------------------------------------------------- 2. beta headers fwd'd
hdr 2 "anthropic-beta / anthropic-version forwarded without 400"
R2=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" \
  -H "anthropic-beta: prompt-caching-2024-07-31" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}")
[ "$R2" = "200" ] && ok "unknown/extra anthropic-beta tolerated (200)" \
                  || bad "anthropic-beta caused $R2 — Claude Code will break on new releases"

# ------------------------------------------------------- 3. PROMPT CACHING ***
hdr 3 "cache_control survives (COST-CRITICAL — fails silently)"
# Must exceed the largest per-model minimum cacheable prefix. Haiku 4.5 does NOT
# cache at ~2.9k tokens but does at ~12k, so keep this generous.
BIG=$(python3 -c "print(('You are a lab assistant for an agentic engineering course covering tool use, MCP and evaluation. ' * 600))")
REQ=$(python3 - "$MODEL" "$BIG" <<'PY'
import json,sys
m,big=sys.argv[1],sys.argv[2]
print(json.dumps({"model":m,"max_tokens":16,
 "system":[{"type":"text","text":big,"cache_control":{"type":"ephemeral"}}],
 "messages":[{"role":"user","content":"Reply: OK"}]}))
PY
)
curl -s -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" -d "$REQ" >/dev/null
sleep 2
U3=$(curl -s -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" -d "$REQ" \
  | python3 -c "import sys,json;u=json.load(sys.stdin).get('usage',{});print(u.get('cache_read_input_tokens',0),u.get('cache_creation_input_tokens',0))" 2>/dev/null || echo "0 0")
CR=$(echo "$U3"|cut -d' ' -f1); CC=$(echo "$U3"|cut -d' ' -f2)
if [ "${CR:-0}" -gt 0 ]; then ok "cache_read_input_tokens=$CR — caching works through the gateway"
elif [ "${CC:-0}" -gt 0 ]; then warn "cache written ($CC) but not read back — re-run; if persistent, caching is broken"
else bad "NO CACHE ACTIVITY — every turn bills as uncached input. Check gateway isn't flattening 'system' blocks."; fi

# ------------------------------------------------------------- 4. streaming
hdr 4 "Streaming works and is incremental (not buffered)"
TMP=$(mktemp)
curl -s -N --max-time 60 -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":200,\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Count slowly from 1 to 40, one number per line.\"}]}" > "$TMP" 2>&1
EV=$(grep -c '^event:' "$TMP" 2>/dev/null || true)
DELTA=$(grep -c 'content_block_delta' "$TMP" 2>/dev/null || true)
PING=$(grep -c 'event: ping' "$TMP" 2>/dev/null || true)
if [ "$EV" -gt 5 ] && [ "$DELTA" -gt 3 ]; then ok "SSE stream: $EV events, $DELTA deltas"
else bad "stream not incremental ($EV events, $DELTA deltas) — Claude Code will stall"; fi
if [ "$PING" -gt 0 ]; then ok "keep-alive pings present ($PING)"
else warn "no 'event: ping' seen. Bedrock sends none; if the gateway doesn't synthesise them, Claude Code aborts a stream after 300s of silence during long thinking pauses."; fi
rm -f "$TMP"

# ------------------------------------------------- 5. error bodies unmodified
hdr 5 "Upstream error bodies forwarded in Anthropic shape"
E5=$(curl -s -X POST "$BASE/v1/messages" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[]}")
if echo "$E5" | grep -q '"type"[[:space:]]*:[[:space:]]*"error"'; then ok "error uses Anthropic {type:error} envelope"
else warn "error not in Anthropic shape — Claude Code's auto-recovery matches on upstream wording: $(echo "$E5"|head -c 160)"; fi

# ------------------------------------------------------------ 6. count_tokens
hdr 6 "count_tokens endpoint (optional — affects /context accuracy)"
C6=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/messages/count_tokens" -H "$H_AUTH" -H "$H_VER" -H "$H_JSON" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}")
[ "$C6" = "200" ] && ok "count_tokens returns 200" || warn "count_tokens returned $C6 — /context shows estimates only"

# ------------------------------------------- 7. model discovery filter (bonus)
hdr 7 "Model discovery: ids must contain 'claude' or 'anthropic'"
IDS=$(curl -s "$BASE/v1/models" -H "$H_AUTH" | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except: print(''); raise SystemExit
print(' '.join(m.get('id','') for m in d.get('data',[])))" 2>/dev/null)
if [ -n "$IDS" ]; then
  BADN=0; for m in $IDS; do case "$m" in *claude*|*anthropic*) ;; *) BADN=$((BADN+1));; esac; done
  [ "$BADN" -eq 0 ] && ok "all model ids pass the discovery filter: $IDS" \
                    || warn "$BADN model id(s) would be dropped by Claude Code discovery"
else warn "/v1/models returned nothing"; fi

echo; echo "──────────────────────────────────────────"
echo "  PASS $PASS   WARN $WARN   FAIL $FAIL"
echo "──────────────────────────────────────────"
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
