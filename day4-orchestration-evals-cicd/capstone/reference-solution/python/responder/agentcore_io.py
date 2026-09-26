"""The two AgentCore adapters, standard library only (the runtime passes in its boto3 client), so they are
tested offline like everything else.

ConverseModel     the agent loop speaks the Anthropic Messages shape in both modes; this translates to Bedrock
                  Converse. The Bedrock Guardrail is on EVERY call (guardrailConfig) - a prompt change cannot
                  remove it; an intervention comes back as stop_reason "guardrail_intervened" and the loop ends.
GatewayOpsReader  the reads from the AgentCore Gateway's ops-read tools over MCP (JSON-RPC tools/call over
                  HTTPS) with this runtime's Identity token. Cedar decides which tools exist for this identity:
                  with the investigator's read scope there is no write tool at all. The Gateway's own aira-ops
                  credential can read EVERY account, so the tenant boundary is enforced in code (sla, tools).
"""
import json, urllib.error, urllib.request, uuid

from .agent import ModelError
from .ops import OpsError

TOOL_PREFIX = "ops-read___"


class ConverseModel:
    def __init__(self, converse, model_id, guardrail_id, guardrail_version, telemetry=None):
        self.converse, self.model_id = converse, model_id
        self.guardrail_id, self.guardrail_version = guardrail_id, guardrail_version
        self.telemetry = telemetry

    def __call__(self, messages, tools, system, max_tokens):
        req = {"modelId": self.model_id, "system": [{"text": system}], "messages": to_converse(messages),
               "inferenceConfig": {"maxTokens": max_tokens},
               "guardrailConfig": {"guardrailIdentifier": self.guardrail_id, "guardrailVersion": self.guardrail_version,
                                   "trace": "enabled"}}
        if tools:
            req["toolConfig"] = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                                         "inputSchema": {"json": t["input_schema"]}}} for t in tools]}
        call = lambda: from_converse(self._send(req))      # noqa: E731
        return self.telemetry.chat(self.model_id, system, messages, call) if self.telemetry else call()

    def _send(self, req):
        try:
            return self.converse(**req)
        except Exception as e:                      # botocore ClientError: status + message, never the request
            meta = getattr(e, "response", {}) or {}
            status = (meta.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0)
            msg = (meta.get("Error") or {}).get("Message") or type(e).__name__
            raise ModelError(status, f"{type(e).__name__}: {msg}") from None


def to_converse(messages):
    """Messages API messages -> Converse messages."""
    out = []
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content, blocks = m.get("content"), []
        if isinstance(content, str):
            blocks.append({"text": content})
        else:
            for b in content or []:
                t = b.get("type")
                if t == "text" and (b.get("text") or "").strip():
                    blocks.append({"text": b["text"]})
                elif t == "tool_use":
                    blocks.append({"toolUse": {"toolUseId": b.get("id", ""), "name": b.get("name", ""), "input": b.get("input") or {}}})
                elif t == "tool_result":
                    blocks.append({"toolResult": {"toolUseId": b.get("tool_use_id", ""),
                                                  "content": [{"text": b.get("content") or "(empty)"}],
                                                  "status": "error" if b.get("is_error") else "success"}})
        out.append({"role": role, "content": blocks or [{"text": "(empty)"}]})
    return out


def from_converse(resp):
    """Converse response -> a Messages API response: content, stop_reason, usage."""
    content = []
    for b in ((resp.get("output") or {}).get("message") or {}).get("content") or []:
        if "text" in b:
            content.append({"type": "text", "text": b["text"]})
        elif "toolUse" in b:
            tu = b["toolUse"]
            content.append({"type": "tool_use", "id": tu.get("toolUseId", ""), "name": tu.get("name", ""),
                            "input": integral(tu.get("input") or {})})
    u = resp.get("usage") or {}
    return {"content": content, "stop_reason": resp.get("stopReason", ""),
            "usage": {"input_tokens": u.get("inputTokens", 0), "output_tokens": u.get("outputTokens", 0)}}


def integral(v):
    """Numbers in tool inputs arrive as Document numbers: an integral value must become an integer (found live in
    Java: 275 arrived as 275.0 and the contract refused it three times - the model could not fix what it had not done)."""
    if isinstance(v, dict):
        return {k: integral(x) for k, x in v.items()}
    if isinstance(v, list):
        return [integral(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


class GatewayOpsReader:
    def __init__(self, gateway_url, bearer_token, timeout=30.0, post=None):
        self.url, self.token, self.timeout = gateway_url, bearer_token, timeout
        self._post = post or self._http_post

    def account(self, account_id):
        return self._call("lookup_account", {"account_id": account_id})

    def tickets(self, account_id):
        return self._call("search_tickets", {"account_id": account_id, "limit": 50})

    def ticket(self, ticket_id):
        return self._call("get_ticket", {"ticket_id": ticket_id})

    def jobs(self, account_id):
        return self._call("list_jobs", {"account_id": account_id})

    def config(self, key):
        return self._call("get_config", {"key": key})

    def _call(self, tool, args):
        msg = self._post({"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": "tools/call",
                          "params": {"name": TOOL_PREFIX + tool, "arguments": args}})
        if "error" in msg:                  # a Cedar denial arrives as a JSON-RPC error
            raise OpsError(403, "denied", f"gateway refused {TOOL_PREFIX}{tool}: {(msg['error'] or {}).get('message', '')}")
        result = msg.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))
        try:
            body = json.loads(text) if text.strip() else {}
        except ValueError:
            raise OpsError(502, "invalid", f"{TOOL_PREFIX}{tool} returned non-JSON") from None
        err = body.get("error") if isinstance(body, dict) else None
        if result.get("isError") or isinstance(err, dict):
            err = err if isinstance(err, dict) else {}
            code = err.get("code") or "tool_error"
            raise OpsError(404 if code == "not_found" else 502, code, err.get("message") or text[:200])
        return body

    def _http_post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if "{" not in raw:
                raise OpsError(e.code, "gateway", f"gateway HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise OpsError(0, "unavailable", f"gateway unreachable: {type(e).__name__}") from None
        start, end = raw.find("{"), raw.rfind("}")          # the body may be SSE-framed
        if start < 0:
            raise OpsError(502, "invalid", "gateway returned no JSON")
        return json.loads(raw[start:end + 1])
