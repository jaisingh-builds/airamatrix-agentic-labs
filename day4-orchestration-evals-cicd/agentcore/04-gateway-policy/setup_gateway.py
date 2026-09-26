#!/usr/bin/env python3
"""Step 4 - AgentCore Gateway (tools as MCP) + Policy (Cedar, enforced on every tool call).

    Gateway {PREFIX}-ops-gateway   inbound: Cognito JWT from step 3 (only our 4 clients)
      target ops-read   OpenAPI, GET operations only      -> aira-ops with a READ-ONLY token (token vault)
      target ops-write  add_ticket_comment + set_ingest_concurrency -> aira-ops with a WRITE token (token vault)
      target handbook   Lambda -> Bedrock Knowledge Base retrieve
    Policy engine, ENFORCE mode: policies/*.cedar. Default deny; forbid wins.

The write token sits behind the gateway. Whether a caller may USE the write tools is decided by
Cedar on the caller's scopes and the tool's arguments - not by the agent's prompt.

Needs: AIRA_OPS_URL, AIRA_OPS_READ_TOKEN, AIRA_OPS_WRITE_TOKEN, KB_ID (the trainer gives you these).
"""
import base64, io, json, os, pathlib, sys, time, urllib.parse, urllib.request, uuid, zipfile
from common import ACCOUNT, PREFIX, REGION, client, need, role, save, say, state, wait

HERE = pathlib.Path(__file__).resolve().parent
env = {k: os.environ.get(k) for k in ("AIRA_OPS_URL", "AIRA_OPS_READ_TOKEN", "AIRA_OPS_WRITE_TOKEN", "KB_ID")}
if not all(env.values()):
    sys.exit(f"set {[k for k, v in env.items() if not v]} first")
discovery, clients = need("discovery_url", "clients")
ac, lam = client("bedrock-agentcore-control"), client("lambda")
GW = f"{PREFIX}-ops-gateway"

# ---- 1. the handbook Lambda (RAG as a tool)
kb_arn = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/{env['KB_ID']}"
lrole = role(f"{PREFIX}-handbook-lambda", "lambda.amazonaws.com", {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "*"},
    {"Effect": "Allow", "Action": "bedrock:Retrieve", "Resource": kb_arn}]})
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.write(HERE / "handbook_lambda.py", "handbook_lambda.py")
fn_name = f"{PREFIX}-handbook-search"
try:
    lam.get_function(FunctionName=fn_name)
    lam.update_function_code(FunctionName=fn_name, ZipFile=buf.getvalue())
    lam.get_waiter("function_updated_v2").wait(FunctionName=fn_name)
except lam.exceptions.ResourceNotFoundException:
    lam.create_function(FunctionName=fn_name, Runtime="python3.12", Role=lrole, Handler="handbook_lambda.handler",
                        Code={"ZipFile": buf.getvalue()}, Timeout=20, MemorySize=256, Architectures=["arm64"],
                        Environment={"Variables": {"KB_ID": env["KB_ID"]}})
lam.get_waiter("function_active_v2").wait(FunctionName=fn_name)
fn_arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
say("lambda  ", fn_arn)

# ---- 2. aira-ops credentials in the AgentCore Identity token vault (never in the agents)
def api_key(name, key):
    have = [p for p in ac.list_api_key_credential_providers().get("credentialProviders", []) if p["name"] == name]
    r = (ac.update_api_key_credential_provider if have else ac.create_api_key_credential_provider)(name=name, apiKey=key)
    return r["credentialProviderArn"], r["apiKeySecretArn"]["secretArn"]
read_prov, read_secret = api_key(f"{PREFIX}-ops-read-key", env["AIRA_OPS_READ_TOKEN"])
write_prov, write_secret = api_key(f"{PREFIX}-ops-write-key", env["AIRA_OPS_WRITE_TOKEN"])
say("vault    read + write aira-ops keys stored")

# ---- 3. the policy engine
pe_name = f"{PREFIX.replace('-', '_')}_ops_policies"
engines = [e for e in ac.list_policy_engines().get("policyEngines", []) if e["name"] == pe_name]
pe_id = engines[0]["policyEngineId"] if engines else ac.create_policy_engine(
    name=pe_name, description="Who may call which aira-ops tool, with which arguments")["policyEngineId"]
pe = wait(lambda: ac.get_policy_engine(policyEngineId=pe_id), ok=("ACTIVE",), bad=("FAILED", "CREATE_FAILED"), what="policy engine")
pe_arn = pe["policyEngineArn"]
say("policy   engine", pe_id)

# ---- 4. the gateway (its role: invoke the Lambda, read the vault, evaluate Cedar)
gw_role = role(f"{PREFIX}-gateway", "bedrock-agentcore.amazonaws.com", {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": fn_arn},
    {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceApiKey", "bedrock-agentcore:GetWorkloadAccessToken",
                                   "bedrock-agentcore:GetWorkloadAccessTokenForJWT"],
     "Resource": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:*"},
    {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": [read_secret, write_secret]},
    {"Effect": "Allow", "Action": "bedrock-agentcore:GetPolicyEngine", "Resource": pe_arn},
    {"Effect": "Allow", "Action": ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
     "Resource": [pe_arn, f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/*"]}]},
    {"Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}})
auth = {"customJWTAuthorizer": {"discoveryUrl": discovery, "allowedClients": [c["client_id"] for c in clients.values()]}}
found = [g for g in ac.list_gateways().get("items", []) if g["name"] == GW]
if found:
    gw_id = found[0]["gatewayId"]
    ac.update_gateway(gatewayIdentifier=gw_id, name=GW, roleArn=gw_role, protocolType="MCP", authorizerType="CUSTOM_JWT",
                      authorizerConfiguration=auth, exceptionLevel="DEBUG",
                      policyEngineConfiguration={"arn": pe_arn, "mode": "ENFORCE"})
else:
    gw_id = ac.create_gateway(name=GW, roleArn=gw_role, protocolType="MCP", authorizerType="CUSTOM_JWT",
                              authorizerConfiguration=auth, exceptionLevel="DEBUG",
                              description="aira-ops tools + handbook, Cedar-enforced (Day 4)",
                              policyEngineConfiguration={"arn": pe_arn, "mode": "ENFORCE"})["gatewayId"]
gw = wait(lambda: ac.get_gateway(gatewayIdentifier=gw_id), ok=("READY",), what="gateway")
gw_arn, gw_url = gw["gatewayArn"], gw["gatewayUrl"]
say("gateway ", gw_id, gw_url)

# ---- 5. targets
spec = json.loads((HERE / "aira-ops.openapi.json").read_text())
spec["servers"] = [{"url": env["AIRA_OPS_URL"].rstrip("/")}]
def subset(keep):
    s = json.loads(json.dumps(spec)); paths = {}
    for p, ms in s["paths"].items():
        kept = {m: o for m, o in ms.items() if isinstance(o, dict) and o.get("operationId") in keep}
        if kept: paths[p] = kept
    s["paths"] = paths
    return json.dumps(s)
READ_OPS = {"search_tickets", "get_ticket", "lookup_account", "list_jobs", "list_config", "get_config"}
WRITE_OPS = {"add_ticket_comment", "set_ingest_concurrency"}
def key_cred(prov):
    return [{"credentialProviderType": "API_KEY", "credentialProvider": {"apiKeyCredentialProvider": {
        "providerArn": prov, "credentialParameterName": "Authorization", "credentialPrefix": "Bearer",
        "credentialLocation": "HEADER"}}}]
handbook_tool = [{"name": "search_handbook",
                  "description": "Search the AiraMatrix ops handbook (runbooks, SLA, on-call, config-change policy, "
                                 "postmortems). Returns passages with their source to cite. Use before answering any "
                                 "question about procedure, policy or who may approve what.",
                  "inputSchema": {"type": "object", "required": ["query"], "properties": {
                      "query": {"type": "string", "description": "A natural-language question"},
                      "max_results": {"type": "integer", "description": "1-8, default 4"}}}}]
TARGETS = {
    "ops-read": ({"mcp": {"openApiSchema": {"inlinePayload": subset(READ_OPS)}}}, key_cred(read_prov)),
    "ops-write": ({"mcp": {"openApiSchema": {"inlinePayload": subset(WRITE_OPS)}}}, key_cred(write_prov)),
    "handbook": ({"mcp": {"lambda": {"lambdaArn": fn_arn, "toolSchema": {"inlinePayload": handbook_tool}}}},
                 [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]),
}
have = {t["name"]: t["targetId"] for t in ac.list_gateway_targets(gatewayIdentifier=gw_id).get("items", [])}
for name, (cfg, cred) in TARGETS.items():
    if name in have:
        ac.update_gateway_target(gatewayIdentifier=gw_id, targetId=have[name], name=name,
                                 targetConfiguration=cfg, credentialProviderConfigurations=cred)
        tid = have[name]
    else:
        tid = ac.create_gateway_target(gatewayIdentifier=gw_id, name=name, targetConfiguration=cfg,
                                       credentialProviderConfigurations=cred)["targetId"]
    wait(lambda: ac.get_gateway_target(gatewayIdentifier=gw_id, targetId=tid), ok=("READY",), what=f"target {name}", pause=4)
    say("target  ", name)

# ---- 6. the Cedar policies
existing = {}
for p in ac.list_policies(policyEngineId=pe_id).get("policies", []):
    if p["status"].endswith("FAILED"):         # a rejected policy can't be updated - remove it and create it again
        ac.delete_policy(policyEngineId=pe_id, policyId=p["policyId"]); time.sleep(3)
    else:
        existing[p["name"]] = p["policyId"]
for f in sorted((HERE / "policies").glob("*.cedar")):
    name = f.stem.replace("-", "_")[3:] if f.stem[:2].isdigit() else f.stem.replace("-", "_")
    stmt = f.read_text().replace("{GATEWAY_ARN}", gw_arn)
    definition = {"cedar": {"statement": stmt}}
    if name in existing:
        ac.update_policy(policyEngineId=pe_id, policyId=existing[name], definition=definition)
        pid = existing[name]
    else:
        pid = ac.create_policy(policyEngineId=pe_id, name=name, definition=definition,
                               validationMode="FAIL_ON_ANY_FINDINGS", description=f"from policies/{f.name}")["policyId"]
    p = wait(lambda: ac.get_policy(policyEngineId=pe_id, policyId=pid), ok=("ACTIVE",),
             bad=("FAILED", "CREATE_FAILED", "UPDATE_FAILED"), what=f"policy {name}", pause=3)
    say("policy  ", name, p["status"])

save(gateway_id=gw_id, gateway_arn=gw_arn, gateway_url=gw_url, policy_engine_id=pe_id, policy_engine_arn=pe_arn,
     handbook_lambda=fn_arn)

# ---- 7. prove it: each role, one allowed and one refused call
import secrets as _s
approver = json.loads((HERE.parent / "out" / "approver.json").read_text())
def token(role_name):
    if role_name == "approver":
        cid, sec, scope = approver["client_id"], approver["client_secret"], approver["scope"]
    else:
        cog = client("cognito-idp"); c = clients[role_name]
        cid = c["client_id"]; scope = " ".join(c["scopes"])
        sec = cog.describe_user_pool_client(UserPoolId=state()["user_pool"], ClientId=cid)["UserPoolClient"]["ClientSecret"]
    b = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(state()["token_url"], method="POST",
                                 data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode(),
                                 headers={"Authorization": f"Basic {b}", "Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=20))["access_token"]
def mcp(tok, method, params):
    req = urllib.request.Request(gw_url, method="POST", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode()[:200]}
def call(tok, tool, args):
    r = mcp(tok, "tools/call", {"name": tool, "arguments": args})
    if "result" in r:
        txt = (r["result"].get("content") or [{}])[0].get("text", "")
        return ("TOOL-ERROR " if r["result"].get("isError") else "ALLOWED ") + txt.replace("\n", " ")[:110]
    return "DENIED " + json.dumps(r.get("error") or r)[:140]
time.sleep(5)
for who in ("investigator", "supervisor", "approver"):
    t = token(who)
    tools = [x["name"] for x in mcp(t, "tools/list", {}).get("result", {}).get("tools", [])]
    say(f"\n  {who}: tools/list shows {len(tools)}: {', '.join(sorted(tools))}")
    say("   get_config           ->", call(t, "ops-read___get_config", {"key": "ingest.max_concurrent_jobs"}))
    say("   add_ticket_comment   ->", call(t, "ops-write___add_ticket_comment",
                                           {"ticket_id": "T-1005", "Idempotency-Key": str(uuid.uuid4()),
                                            "comment": f"[policy test by {who}] please ignore"}) if who != "approver" else "(skipped)")
    say("   set_ingest_conc 32   ->", call(t, "ops-write___set_ingest_concurrency",
                                           {"value": 32, "expected_version": 999, "Idempotency-Key": str(uuid.uuid4())}))
    say("   set_ingest_conc 16   ->", call(t, "ops-write___set_ingest_concurrency",
                                           {"value": 16, "expected_version": 999, "Idempotency-Key": str(uuid.uuid4())}))
