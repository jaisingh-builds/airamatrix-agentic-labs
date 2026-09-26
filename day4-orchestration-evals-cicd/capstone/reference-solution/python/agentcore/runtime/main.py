"""The SLA-breach responder as an AgentCore Runtime (direct code deploy, PYTHON_3_12, like agentcore/06-agents).

One invocation = one run of the SAME responder as local mode (responder.responder.run):
    Identity token (investigator: read scope) -> Gateway reads over MCP -> Bedrock Converse with the Guardrail
    on every call -> the code guardrail -> a status. Nothing here can write: no write tool, no write token.

Payload: {"prompt", "actor_id", "account_id", "as_of", "run_id"?} - the CALLER binds the tenant and the clock;
the model never chooses them. Response: the run record + "mode": "agentcore" + "trace": [the JSONL span
records], so the caller keeps one trace format for both modes.

Environment (set by agentcore/capstone_aws.py deploy - never secrets): AWS_REGION, GATEWAY_URL, OAUTH_PROVIDER,
OAUTH_SCOPES, MODEL_ID, GUARDRAIL_ID, GUARDRAIL_VERSION, MAX_BUDGET_USD, MAX_TURNS, LAB_TRACE_DIR.
"""
import json, os, re, uuid
from pathlib import Path

import boto3
from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.responses import JSONResponse

from responder import responder, store
from responder.agent import ResponderAgent, Telemetry
from responder.agentcore_io import ConverseModel, GatewayOpsReader
from responder.repo import spans
from responder.util import ArgError, dumps, iso, parse_instant

REGION = os.environ.get("AWS_REGION", "ap-south-1")
ACCOUNT = re.compile(r"^ACC-\d{4}$")
RUN_ID = re.compile(r"^[0-9a-f]{10}$")

app = BedrockAgentCoreApp()
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def _need(k):
    v = os.environ.get(k, "").strip()
    if not v:
        raise RuntimeError(f"{k} is not set - the deploy step sets it")
    return v


@requires_access_token(provider_name=os.environ.get("OAUTH_PROVIDER", "unset"),
                       scopes=os.environ.get("OAUTH_SCOPES", "").split(), auth_flow="M2M")
def gateway_token(*, access_token: str) -> str:
    """AgentCore Identity exchanges this runtime's workload identity for the investigator's M2M token.
    No client secret reaches this code or the package."""
    return access_token


class GenAiTelemetry(Telemetry):
    """OpenTelemetry GenAI spans for CloudWatch GenAI Observability / AgentCore Evaluations (scope
    opentelemetry.instrumentation.*, gen_ai.operation.name and session.id on every span). opentelemetry-instrument
    exports them; without it the API is a no-op. The JSONL trace is written either way."""
    SCOPE = "opentelemetry.instrumentation.aira_capstone"

    def __init__(self, session):
        from opentelemetry import trace
        self.trace, self.session = trace, session
        self.tracer = trace.get_tracer(self.SCOPE, "1.0.0")

    def _span(self, name, op, kind, **attrs):
        s = self.tracer.start_as_current_span(name, kind=kind, attributes={
            "gen_ai.operation.name": op, "session.id": self.session, "agent.role": "sla-responder", **attrs})
        return s

    def agent(self, prompt):
        return self._span("invoke_agent sla-responder", "invoke_agent", self.trace.SpanKind.INTERNAL,
                          **{"gen_ai.agent.name": "sla-responder", "gen_ai.task.input": prompt})

    def chat(self, model, system, messages, call):
        with self._span(f"chat {model}", "chat", self.trace.SpanKind.CLIENT, **{
                "gen_ai.system": "aws.bedrock", "gen_ai.request.model": model, "gen_ai.system_instructions": system,
                "gen_ai.input.messages": dumps(messages)}) as s:
            out = call()
            s.set_attribute("gen_ai.output.messages", dumps(out["content"]))
            s.set_attribute("gen_ai.response.finish_reasons", out["stop_reason"])
            s.set_attribute("gen_ai.usage.input_tokens", out["usage"]["input_tokens"])
            s.set_attribute("gen_ai.usage.output_tokens", out["usage"]["output_tokens"])
            return out

    def tool(self, name, input_, call):
        with self._span(f"execute_tool {name}", "execute_tool", self.trace.SpanKind.INTERNAL, **{
                "gen_ai.tool.name": name, "gen_ai.tool.call.arguments": dumps(input_)}) as s:
            r = call()
            s.set_attribute("gen_ai.tool.call.result", r.text)
            if r.error:
                s.set_attribute("error.type", "tool_error")
            return r


def run_invocation(payload, session, token_fn=gateway_token, converse=None, telemetry=None):
    """Validate -> run the responder -> the run record + its trace records. Separate from the entrypoint for tests."""
    account, as_of_text = str(payload.get("account_id") or ""), str(payload.get("as_of") or "")
    if not ACCOUNT.match(account) or not as_of_text.strip():
        raise ArgError("account_id and as_of are required")
    as_of = parse_instant(as_of_text)
    prompt = str(payload.get("prompt") or "")
    if len(prompt) > 2000:
        raise ArgError("prompt is over 2000 characters")
    rid = str(payload.get("run_id") or "")
    rid = rid if RUN_ID.match(rid) else store.new_id()

    tel = telemetry or Telemetry()
    tr = spans.Tracer("capstone", trace_id=rid)
    model = ConverseModel(converse or _bedrock.converse, _need("MODEL_ID"), _need("GUARDRAIL_ID"), _need("GUARDRAIL_VERSION"),
                          tel if hasattr(tel, "chat") else None)
    agent = ResponderAgent(model, "claude-sonnet", int(os.environ.get("MAX_TURNS", "10")),
                           float(os.environ.get("MAX_BUDGET_USD", "0.40")), os.environ, tel)
    reads = GatewayOpsReader(_need("GATEWAY_URL"), token_fn())
    o = responder.run(rid, account, as_of, prompt, reads, agent, tr)
    out = o.to_json()
    out.update(mode="agentcore", account_id=account, as_of=iso(as_of))
    try:
        out["trace"] = [json.loads(l) for l in Path(tr.path).read_text(encoding="utf-8").splitlines() if l.strip()]
        Path(tr.path).unlink()
    except Exception as e:
        out["trace_error"] = type(e).__name__
    return out


@app.entrypoint
def invoke(payload, context):
    session = getattr(context, "session_id", None) or str(uuid.uuid4())
    payload = payload if isinstance(payload, dict) else {}
    try:
        tel = GenAiTelemetry(session)
    except Exception:            # opentelemetry not importable (a laptop run): the JSONL trace still works
        tel = Telemetry()
    try:
        if isinstance(tel, GenAiTelemetry):
            with tel.agent(str(payload.get("prompt") or "")) as span:
                out = run_invocation(payload, session, telemetry=tel)
                span.set_attribute("gen_ai.task.output", out["status"])
                return out
        return run_invocation(payload, session, telemetry=tel)
    except ArgError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": spans.redact(str(e), limit=None)}, status_code=400)


if __name__ == "__main__":
    app.run()
