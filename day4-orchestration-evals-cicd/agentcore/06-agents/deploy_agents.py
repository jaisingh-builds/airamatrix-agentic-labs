"""Step 6 - deploy the three agents to AgentCore Runtime (direct code deploy, no Docker).

    PYTHONPATH=.. python deploy_agents.py            # build, upload, create or update all three
    PYTHONPATH=.. python deploy_agents.py --no-build # code-only change: skip pip, re-package main.py

What it does:
  1. pip-installs agent/requirements.txt for linux/arm64 into build/, adds agent/main.py, zips it
  2. uploads the zip to s3://{PREFIX}-agentcore-{ACCOUNT}/
  3. one IAM role per agent, least privilege (the supervisor alone may invoke the others and use Memory)
  4. creates/updates the investigator and reviewer runtimes, then the supervisor (it needs their ARNs)
"""
import json, pathlib, shutil, subprocess, sys, zipfile

from common import ACCOUNT, MODEL_ID, PREFIX, REGION, client, need, role, save, say, state, wait

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"
ZIP = BUILD / "agent.zip"
PYVER = "3.12"

gw_url, providers, clients, g_id, g_ver, mem_arn, mem_id = need(
    "gateway_url", "providers", "clients", "guardrail_id", "guardrail_version", "memory_arn", "memory_id")
ac = client("bedrock-agentcore-control")
s3 = client("s3")
bucket = f"{PREFIX}-agentcore-{ACCOUNT}"
prefix_ = PREFIX.replace("-", "_")


def build(install=True):
    pkg = BUILD / "pkg"
    if install or not pkg.exists():
        shutil.rmtree(pkg, ignore_errors=True); pkg.mkdir(parents=True)
        say("build    pip install for linux/arm64 (first run ~1 minute)")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "agent/requirements.txt"),
                        "--target", str(pkg), "--platform", "manylinux2014_aarch64", "--implementation", "cp",
                        "--python-version", PYVER, "--only-binary=:all:", "--upgrade"], check=True)
    shutil.copy(HERE / "agent/main.py", pkg / "main.py")
    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pkg.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(pkg))
    say(f"build    {ZIP.name} {ZIP.stat().st_size // 1_000_000} MB")


def upload():
    try:
        s3.head_bucket(Bucket=bucket)
    except s3.exceptions.ClientError:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    key = "agents/agent.zip"
    s3.upload_file(str(ZIP), bucket, key)
    say(f"upload   s3://{bucket}/{key}")
    return key


def policy_for(name):
    rt = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}"
    stmts = [
        {"Sid": "Model", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:::foundation-model/*",
                      f"arn:aws:bedrock:*:{ACCOUNT}:inference-profile/*"]},
        {"Sid": "Guardrail", "Effect": "Allow", "Action": "bedrock:ApplyGuardrail",
         "Resource": f"arn:aws:bedrock:{REGION}:{ACCOUNT}:guardrail/{g_id}"},
        {"Sid": "Identity", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"],
         "Resource": [f"{rt}:workload-identity-directory/default", f"{rt}:workload-identity-directory/default/*",
                      f"{rt}:token-vault/default", providers[name]["arn"]]},
        {"Sid": "ProviderSecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
         "Resource": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:bedrock-agentcore-identity!default/oauth2/{providers[name]['name']}*"},
        {"Sid": "Logs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups"],
         "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:*"},
        {"Sid": "Traces", "Effect": "Allow",
         "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules",
                    "xray:GetSamplingTargets"], "Resource": "*"},
        {"Sid": "Metrics", "Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
    ]
    if name == "supervisor":
        stmts += [
            {"Sid": "CallSpecialists", "Effect": "Allow",
             "Action": ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"],
             "Resource": [f"{rt}:runtime/{prefix_}_investigator-*", f"{rt}:runtime/{prefix_}_reviewer-*"]},
            {"Sid": "Memory", "Effect": "Allow",
             "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:ListSessions", "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:GetMemoryRecord",
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:DeleteEvent"],   # the guardrail redacts a blocked turn: delete + rewrite
             "Resource": mem_arn}]
    return {"Version": "2012-10-17", "Statement": stmts}


def deploy(name, key, extra_env=None):
    arn = role(f"{PREFIX}-agent-{name}", "bedrock-agentcore.amazonaws.com", policy_for(name),
               extra_trust={"Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}})
    env = {"ROLE": name, "GATEWAY_URL": gw_url, "OAUTH_PROVIDER": providers[name]["name"],
           "OAUTH_SCOPES": " ".join(clients[name]["scopes"]), "MODEL_ID": MODEL_ID,
           "GUARDRAIL_ID": g_id, "GUARDRAIL_VERSION": g_ver, "AGENT_OBSERVABILITY_ENABLED": "true",
           **(extra_env or {})}
    spec = dict(agentRuntimeArtifact={"codeConfiguration": {
                    "code": {"s3": {"bucket": bucket, "prefix": key}},
                    "runtime": "PYTHON_3_12", "entryPoint": ["opentelemetry-instrument", "main.py"]}},
                roleArn=arn, networkConfiguration={"networkMode": "PUBLIC"},
                protocolConfiguration={"serverProtocol": "HTTP"}, environmentVariables=env,
                description=f"Day 4 {name} agent")
    rname = f"{prefix_}_{name}"
    found = [r for r in ac.list_agent_runtimes()["agentRuntimes"] if r["agentRuntimeName"] == rname]
    if found:
        rid = found[0]["agentRuntimeId"]
        ac.update_agent_runtime(agentRuntimeId=rid, **spec)
        say(f"runtime  {name:<12} updating {rid}")
    else:
        rid = ac.create_agent_runtime(agentRuntimeName=rname, **spec)["agentRuntimeId"]
        say(f"runtime  {name:<12} creating {rid}")
    r = wait(lambda: ac.get_agent_runtime(agentRuntimeId=rid), ok=("READY",),
             bad=("CREATE_FAILED", "UPDATE_FAILED"), what=f"runtime {name}", pause=10)
    say(f"runtime  {name:<12} READY  {r['agentRuntimeArn']}")
    return r["agentRuntimeArn"]


build(install="--no-build" not in sys.argv)       # --no-build: keep the installed packages, re-add main.py
key = upload()
arns = {n: deploy(n, key) for n in ("investigator", "reviewer")}
arns["supervisor"] = deploy("supervisor", key, {"INVESTIGATOR_ARN": arns["investigator"],
                                                "REVIEWER_ARN": arns["reviewer"], "MEMORY_ID": mem_id})
save(runtimes=arns, agent_bucket=bucket)
say("saved    runtimes ->", ", ".join(arns))
