"""Step 10 - delete everything steps 2-9 created, in reverse dependency order.

    PYTHONPATH=.. python teardown.py            # shows what it would delete
    PYTHONPATH=.. python teardown.py --yes      # deletes

Reads out/state.json, so it deletes only what YOUR prefix created. Each item is best-effort: something already
gone is reported and skipped. Step 1 (Transaction Search) is account-wide and shared - it is left on.
The Knowledge Base (step 0) belongs to the trainer and is never touched.
"""
import sys, time
import boto3
from common import ACCOUNT, PREFIX, STATE, client, say, state

s = state()
go = "--yes" in sys.argv
ac, iam = client("bedrock-agentcore-control"), boto3.client("iam")


def step(what, fn):
    if not go:
        say("would delete", what); return
    try:
        fn(); say("deleted ", what)
    except Exception as e:                       # already gone, or never created
        say("skipped ", what, "-", str(e).split(":")[-1].strip()[:90])


def drop_role(name):
    for p in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=p)
    iam.delete_role(RoleName=name)


# 9 dashboard
if "dashboard" in s:
    step(f"dashboard {s['dashboard']}", lambda: client("cloudwatch").delete_dashboards(DashboardNames=[s["dashboard"]]))
# 8 evaluations
for agent, cfg in s.get("online_evals", {}).items():
    step(f"online evaluation {cfg['id']}", lambda c=cfg: ac.delete_online_evaluation_config(onlineEvaluationConfigId=c["id"]))
step(f"role {PREFIX}-evaluation", lambda: drop_role(f"{PREFIX}-evaluation"))
# 6 runtimes
for agent, arn in s.get("runtimes", {}).items():
    step(f"runtime {agent}", lambda a=arn: ac.delete_agent_runtime(agentRuntimeId=a.rsplit("/", 1)[1]))
    step(f"role {PREFIX}-agent-{agent}", lambda a=agent: drop_role(f"{PREFIX}-agent-{a}"))
if "agent_bucket" in s:
    def drop_bucket():
        b = boto3.resource("s3").Bucket(s["agent_bucket"])
        b.object_versions.delete(); b.delete()
    step(f"bucket {s['agent_bucket']}", drop_bucket)
# 5 memory
if "memory_id" in s:
    step(f"memory {s['memory_id']}", lambda: ac.delete_memory(memoryId=s["memory_id"]))
# 4 gateway + policy
if "gateway_id" in s:
    def drop_gateway():
        for t in ac.list_gateway_targets(gatewayIdentifier=s["gateway_id"])["items"]:
            ac.delete_gateway_target(gatewayIdentifier=s["gateway_id"], targetId=t["targetId"])
        time.sleep(10)
        ac.delete_gateway(gatewayIdentifier=s["gateway_id"])
    step(f"gateway {s['gateway_id']} (+ targets)", drop_gateway)
if "policy_engine_id" in s:
    def drop_engine():
        pe = s["policy_engine_id"]
        for p in ac.list_policies(policyEngineId=pe)["policies"]:
            ac.delete_policy(policyEngineId=pe, policyId=p["policyId"])
        time.sleep(15)
        ac.delete_policy_engine(policyEngineId=pe)
    step(f"policy engine {s['policy_engine_id']} (+ policies)", drop_engine)
for key in ("ops-read-key", "ops-write-key"):
    step(f"API key provider {PREFIX}-{key}", lambda k=key: ac.delete_api_key_credential_provider(name=f"{PREFIX}-{k}"))
step(f"role {PREFIX}-gateway", lambda: drop_role(f"{PREFIX}-gateway"))
step(f"lambda {PREFIX}-handbook-search", lambda: client("lambda").delete_function(FunctionName=f"{PREFIX}-handbook-search"))
step(f"role {PREFIX}-handbook-lambda", lambda: drop_role(f"{PREFIX}-handbook-lambda"))
# 3 identity
for agent, p in s.get("providers", {}).items():
    step(f"OAuth provider {p['name']}", lambda n=p["name"]: ac.delete_oauth2_credential_provider(name=n))
if "user_pool" in s:
    def drop_pool():
        cog = client("cognito-idp")
        cog.delete_user_pool_domain(Domain=f"{PREFIX}-agents-{ACCOUNT}", UserPoolId=s["user_pool"])
        cog.delete_user_pool(UserPoolId=s["user_pool"])
    step(f"Cognito user pool {s['user_pool']} (+ domain, clients)", drop_pool)
# 2 guardrail
if "guardrail_id" in s:
    step(f"guardrail {s['guardrail_id']}", lambda: client("bedrock").delete_guardrail(guardrailIdentifier=s["guardrail_id"]))

if go:
    STATE.rename(STATE.with_suffix(".deleted.json"))
    (STATE.parent / "approver.json").unlink(missing_ok=True)
    say("done     state moved to out/state.deleted.json; approver secret removed")
else:
    say("dry run  - re-run with --yes to delete")
