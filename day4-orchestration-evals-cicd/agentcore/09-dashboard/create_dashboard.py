"""Step 9 - one CloudWatch dashboard for the whole system.

    PYTHONPATH=.. python create_dashboard.py

AgentCore publishes metrics without any code from you - this step only arranges them:
  AWS/Bedrock-AgentCore          runtime invocations/latency/errors, gateway tool calls, policy allow/deny,
                                 identity token fetches, memory records
  AWS/Bedrock/Guardrails         guardrail invocations vs interventions
  AWS/Bedrock                    model tokens
  Bedrock-AgentCore/Evaluations  the scores from step 8 (online + batch)
The per-request story (which tool, which policy, which span) is in GenAI Observability - linked at the top.
"""
import json
from common import ACCOUNT, PREFIX, REGION, client, need, save, say, state

runtimes, gw_arn, gw_id, pe_id, g_id, g_ver, mem_arn, providers = need(
    "runtimes", "gateway_arn", "gateway_id", "policy_engine_id", "guardrail_id", "guardrail_version",
    "memory_arn", "providers")
# the Java agents (06-agents-java), when deployed, sit next to the Python ones on the same graphs
runtimes = {**runtimes, **{f"java {a}": arn for a, arn in state().get("java_runtimes", {}).items()}}
NS = "AWS/Bedrock-AgentCore"
name = f"{PREFIX}-agentcore"


def rt_metric(metric, agent, arn, stat="Sum", **kw):
    rname = arn.rsplit("/", 1)[1].rsplit("-", 1)[0]
    return [NS, metric, "Resource", arn, "Operation", "InvokeAgentRuntime", "Name", f"{rname}::DEFAULT",
            {"stat": stat, "label": agent, **kw}]


def search(expr, stat, label, period=300, id_="s1"):
    return [[{"expression": f"SEARCH('{expr}', '{stat}', {period})", "label": label, "id": id_}]]


def widget(title, metrics, x, y, w=8, h=6, view="timeSeries", stacked=False, stat=None, extra=None):
    props = {"title": title, "region": REGION, "metrics": metrics, "view": view, "stacked": stacked,
             "period": 300, **(extra or {})}
    if stat:
        props["stat"] = stat
    return {"type": "metric", "x": x, "y": y, "width": w, "height": h, "properties": props}


console = f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}"
widgets = [
    {"type": "text", "x": 0, "y": 0, "width": 24, "height": 2, "properties": {"markdown":
        f"## AiraMatrix ops agents on AgentCore ({PREFIX})\n"
        f"Per-request traces: [GenAI Observability]({console}#gen-ai-observability/agent-core) · "
        f"Scores: [Evaluations]({console}#gen-ai-observability/agent-core/evaluations) · "
        f"Guardrail `{g_id}` v{g_ver} · Gateway `{gw_id}` · Policy engine `{pe_id}`"}},

    widget("Agent invocations", [rt_metric("Invocations", a, arn) for a, arn in runtimes.items()], 0, 2),
    widget("Agent latency p90 (ms)", [rt_metric("Latency", a, arn, stat="p90") for a, arn in runtimes.items()], 8, 2),
    widget("Agent errors", [rt_metric("SystemErrors", a, arn) for a, arn in runtimes.items()] +
           [rt_metric("Errors", a, arn) for a, arn in runtimes.items()], 16, 2),

    widget("Gateway tool calls by tool", search(
        f'{{{NS},Resource,Operation,Method,Protocol,Name}} MetricName="Invocations" Resource="{gw_arn}" Method="tools/call"',
        "Sum", "", id_="tools"), 0, 8, stacked=True),
    widget("Policy decisions (Cedar, ENFORCE)", [
        [NS, "AllowDecisions", "TargetResource", gw_id, "OperationName", "AuthorizeAction", "PolicyEngine", pe_id,
         {"stat": "Sum", "label": "allow", "color": "#2ca02c"}],
        [NS, "DenyDecisions", "TargetResource", gw_id, "OperationName", "AuthorizeAction", "PolicyEngine", pe_id,
         {"stat": "Sum", "label": "deny", "color": "#d62728"}]], 8, 8),
    widget("Denials by determining policy", search(
        f'{{{NS},Policy,TargetResource,OperationName,PolicyEngine}} MetricName="DenyDecisions" TargetResource="{gw_id}"',
        "Sum", "", id_="deny"), 16, 8, view="bar"),

    widget("Guardrail: evaluated vs intervened", [
        ["AWS/Bedrock/Guardrails", "Invocations", "GuardrailArn", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:guardrail/{g_id}",
         "GuardrailVersion", g_ver, {"stat": "Sum", "label": "evaluated"}],
        ["AWS/Bedrock/Guardrails", "InvocationsIntervened", "GuardrailArn",
         f"arn:aws:bedrock:{REGION}:{ACCOUNT}:guardrail/{g_id}", "GuardrailVersion", g_ver,
         {"stat": "Sum", "label": "intervened", "color": "#d62728"}]], 0, 14),
    widget("Claude tokens in / out (whole account)",
           search('{AWS/Bedrock,ModelId} MetricName="InputTokenCount" claude', "Sum", "in", id_="tin") +
           search('{AWS/Bedrock,ModelId} MetricName="OutputTokenCount" claude', "Sum", "out", id_="tout"), 8, 14, stacked=True),
    widget("Identity: OAuth tokens issued to agents", search(
        f'{{{NS},ProviderName,Type,TokenVault}} MetricName="ResourceAccessTokenFetchSuccess"',
        "Sum", "", id_="idt"), 16, 14),

    widget("Evaluation scores (online + batch, 0-1)", search(
        f'{{Bedrock-AgentCore/Evaluations,service.name}} {PREFIX.replace("-", "_")}', "Average", "",
        period=3600, id_="ev"), 0, 20, w=16, extra={"yAxis": {"left": {"min": 0, "max": 1}}}),
    widget("Memory records extracted", search(
        f'{{{NS},ItemType,Resource}} MetricName="CreationCount" Resource="{mem_arn}"', "Sum", "", id_="mem"),
        16, 20),
]

client("cloudwatch").put_dashboard(DashboardName=name, DashboardBody=json.dumps({"widgets": widgets}))
url = f"{console}#dashboards/dashboard/{name}"
say("dashboard", name)
say("open     ", url)
save(dashboard=name)
