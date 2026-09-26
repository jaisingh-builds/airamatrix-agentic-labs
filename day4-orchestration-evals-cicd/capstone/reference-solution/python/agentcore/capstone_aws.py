#!/usr/bin/env python3
"""AgentCore mode - the same responder as an AgentCore Runtime, reusing the shared Day 4 stack READ-ONLY.

    pip install -r agentcore/requirements.txt          # boto3, in a venv: only this tool needs it
    python3 agentcore/capstone_aws.py deploy [--no-build]
    python3 agentcore/capstone_aws.py invoke --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
    python3 agentcore/capstone_aws.py approve RUN --by NAME --reason WHY     (reject too) who = your AWS identity + name
    python3 agentcore/capstone_aws.py gate-check                            the agent identity is DENIED a write
    python3 agentcore/capstone_aws.py eval [--repeat N] [--cases a,b] [--budget USD]
    python3 agentcore/capstone_aws.py teardown --yes
    show / list / trace: the local CLI's commands, same store.

State: shared  agentcore/out/state.json (AC_STATE) - the guardrail, identity, gateway. READ ONLY here.
       own     python/out/capstone-state.json (CAPSTONE_STATE) - what THIS tool created, for teardown.
Account ids, ARNs and ids live only in those gitignored files. Names: runtime {p_}cap_py_responder,
role {p}-capstone-py-runtime, code at s3://<the shared agent bucket>/capstone-py/responder.zip.
"""
import base64, json, os, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from responder import cli, evals, gate, repo  # noqa: E402
from responder.gate import GateError  # noqa: E402
from responder.repo import spans  # noqa: E402
from responder.store import Store, new_id  # noqa: E402
from responder.util import ArgError, SetupError, cut, iso, jround, parse_instant  # noqa: E402

REGION = os.environ.get("AWS_REGION") or "ap-south-1"
PREFIX = os.environ.get("AC_PREFIX") or "aira-d4"
MODEL_ID = os.environ.get("AC_MODEL_ID") or "global.anthropic.claude-sonnet-5"
SHARED = Path(os.environ.get("AC_STATE") or repo.root() / "day4-orchestration-evals-cicd" / "agentcore" / "out" / "state.json")
OWN = Path(os.environ.get("CAPSTONE_STATE") or repo.python_dir() / "out" / "capstone-state.json")
BUILD = repo.python_dir() / "out" / "build"
RUNTIME_NAME = PREFIX.replace("-", "_") + "cap_py_responder"
ROLE_NAME = f"{PREFIX}-capstone-py-runtime"
CODE_KEY = "capstone-py/responder.zip"
PYVER = "3.12"


def say(s):
    print("  " + s, flush=True)


def client(name, **cfg):
    import boto3
    from botocore.config import Config
    return boto3.client(name, region_name=REGION, config=Config(**cfg) if cfg else None)


def account():
    return client("sts").get_caller_identity()["Account"]


# ---------------------------------------------------------------------------------------------- state files

def shared():
    if not SHARED.exists():
        raise SetupError(f"no {SHARED} - the Day 4 AgentCore stack (steps 02-05) must exist; set AC_STATE")
    return json.loads(SHARED.read_text())


def need(dotted):
    n = shared()
    for k in dotted.split("."):
        n = n.get(k) if isinstance(n, dict) else None
    if n in (None, "", [], {}):
        raise SetupError(f"agentcore/out/state.json has no '{dotted}' - run the AgentCore step that creates it")
    return n


def own():
    return json.loads(OWN.read_text()) if OWN.exists() else {}


def save_own(key, value):
    o = own()
    if value is None:
        o.pop(key, None)
    else:
        o[key] = value
    OWN.parent.mkdir(parents=True, exist_ok=True)
    OWN.write_text(json.dumps(o, indent=2) + "\n")
    OWN.chmod(0o600)


# ------------------------------------------------------------------------------------------------- deploy

def build(install=True):
    """pip-installs runtime/requirements.txt for linux/arm64 (AgentCore runs on Graviton), adds main.py, the
    responder package and the two repo modules it reuses (common/spans.py, labkit agentic_core), zips it."""
    pkg = BUILD / "pkg"
    if install or not pkg.exists():
        shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True)
        say("build    pip install for linux/arm64 (first run ~1 minute)")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "runtime" / "requirements.txt"),
                        "--target", str(pkg), "--platform", "manylinux2014_aarch64", "--implementation", "cp",
                        "--python-version", PYVER, "--only-binary=:all:", "--upgrade"], check=True)
    for d in ("responder", "agentic_core"):
        shutil.rmtree(pkg / d, ignore_errors=True)
    shutil.copy(HERE / "runtime" / "main.py", pkg / "main.py")
    shutil.copytree(repo.python_dir() / "responder", pkg / "responder", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repo.root() / "labkit" / "python" / "agentic_core", pkg / "agentic_core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(repo.root() / "day4-orchestration-evals-cicd" / "common" / "spans.py", pkg / "spans.py")
    z = BUILD / "responder.zip"
    z.unlink(missing_ok=True)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pkg.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(pkg))
    say(f"build    {z.name} {z.stat().st_size // 1_000_000} MB")
    return z


def environment():
    """Exactly what runtime/main.py reads."""
    return {"AWS_REGION": REGION, "GATEWAY_URL": need("gateway_url"), "OAUTH_PROVIDER": need("providers.investigator.name"),
            "OAUTH_SCOPES": " ".join(need("clients.investigator.scopes")), "MODEL_ID": MODEL_ID,
            "GUARDRAIL_ID": need("guardrail_id"), "GUARDRAIL_VERSION": need("guardrail_version"),
            "MAX_BUDGET_USD": os.environ.get("CAPSTONE_MAX_BUDGET_USD") or "0.40",
            "MAX_TURNS": os.environ.get("CAPSTONE_MAX_TURNS") or "10",
            "LAB_TRACE_DIR": "/tmp/traces", "AGENT_OBSERVABILITY_ENABLED": "true"}


def policy(acct):
    """The runtime role's one inline policy - the same statements as Java's minus the image pull: model, guardrail,
    the INVESTIGATOR's Identity provider (read scope), logs/traces. No gateway write, no Memory, no other runtime."""
    rt = f"arn:aws:bedrock-agentcore:{REGION}:{acct}"
    prov_arn, prov_name = need("providers.investigator.arn"), need("providers.investigator.name")
    metrics = {"Sid": "Metrics", "Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
               "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}}
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "Model", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:::foundation-model/*",
                      f"arn:aws:bedrock:*:{acct}:inference-profile/*"]},
        {"Sid": "Guardrail", "Effect": "Allow", "Action": "bedrock:ApplyGuardrail",
         "Resource": f"arn:aws:bedrock:{REGION}:{acct}:guardrail/{need('guardrail_id')}"},
        {"Sid": "Identity", "Effect": "Allow",
         "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"],
         "Resource": [f"{rt}:workload-identity-directory/default", f"{rt}:workload-identity-directory/default/*",
                      f"{rt}:token-vault/default", prov_arn]},
        {"Sid": "ProviderSecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
         "Resource": f"arn:aws:secretsmanager:{REGION}:{acct}:secret:bedrock-agentcore-identity!default/oauth2/{prov_name}*"},
        {"Sid": "Logs", "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups"], "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:*"},
        {"Sid": "Traces", "Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                                         "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": "*"},
        metrics]}


def trust(acct):
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                                                    "Action": "sts:AssumeRole",
                                                    "Condition": {"StringEquals": {"aws:SourceAccount": acct}}}]}


def role(acct):
    iam = client("iam")
    fresh = False
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust(acct)))
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust(acct)),
                              Description="Day 4 capstone reference runtime (Python)")["Role"]["Arn"]
        fresh = True
    save_own("role_name", ROLE_NAME)
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="least-privilege", PolicyDocument=json.dumps(policy(acct)))
    say(f"iam      role {ROLE_NAME} {'created' if fresh else 'updated'}")
    if fresh:
        time.sleep(12)            # a brand-new role takes a few seconds before a service can assume it
    return arn


def deploy(args):
    acct = account()
    env = environment()                                 # fails early if the shared stack is missing
    bucket = need("agent_bucket")
    z = build(install="--no-build" not in args)
    client("s3").upload_file(str(z), bucket, CODE_KEY)
    save_own("code", {"bucket": bucket, "key": CODE_KEY})
    say(f"upload   s3://<shared agent bucket>/{CODE_KEY}")
    role_arn = role(acct)
    ac = client("bedrock-agentcore-control")
    spec = dict(agentRuntimeArtifact={"codeConfiguration": {"code": {"s3": {"bucket": bucket, "prefix": CODE_KEY}},
                                                            "runtime": "PYTHON_3_12",
                                                            "entryPoint": ["opentelemetry-instrument", "main.py"]}},
                roleArn=role_arn, networkConfiguration={"networkMode": "PUBLIC"},
                protocolConfiguration={"serverProtocol": "HTTP"}, environmentVariables=env,
                description="Day 4 capstone reference: SLA-breach responder (Python, direct code deploy)")
    rid = find_runtime(ac)
    if rid:
        ac.update_agent_runtime(agentRuntimeId=rid, **spec)
        say(f"runtime  updating {RUNTIME_NAME}")
    else:
        rid = ac.create_agent_runtime(agentRuntimeName=RUNTIME_NAME, **spec)["agentRuntimeId"]
        say(f"runtime  creating {RUNTIME_NAME}")
    rt = {"id": rid, "name": RUNTIME_NAME, "log_group": f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"}
    save_own("runtime", rt)                             # recorded before the wait: teardown works even if it fails
    for _ in range(90):
        r = ac.get_agent_runtime(agentRuntimeId=rid)
        if r["status"] == "READY":
            rt["arn"] = r["agentRuntimeArn"]
            save_own("runtime", rt)
            say(f"runtime  READY    {RUNTIME_NAME}  (arn in {OWN.name})")
            return 0
        if r["status"] in ("CREATE_FAILED", "UPDATE_FAILED"):
            raise SetupError(f"runtime {r['status']}: {r.get('failureReason')}")
        time.sleep(10)
    raise SetupError("runtime still not ready")


def find_runtime(ac):
    token = None
    while True:
        page = ac.list_agent_runtimes(maxResults=100, **({"nextToken": token} if token else {}))
        for r in page.get("agentRuntimes", []):
            if r["agentRuntimeName"] == RUNTIME_NAME:
                return r["agentRuntimeId"]
        token = page.get("nextToken")
        if not token:
            return None


# ------------------------------------------------------------------------------------------------- invoke

def call(acc, as_of, prompt, actor, run_id):
    """InvokeAgentRuntime. A long read timeout and NO automatic retry: an SDK retry after a timeout would start
    a second, parallel run."""
    arn = (own().get("runtime") or {}).get("arn")
    if not arn:
        raise SetupError(f"no runtime in {OWN.name} - run deploy first")
    payload = {"prompt": prompt or "", "actor_id": actor, "account_id": acc, "as_of": as_of, "run_id": run_id}
    rt = client("bedrock-agentcore", read_timeout=900, retries={"total_max_attempts": 1})
    r = rt.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId="capstone-" + uuid.uuid4().hex,
                                runtimeUserId=actor, contentType="application/json",
                                payload=json.dumps(payload).encode())
    return json.loads(r["response"].read())


def record(store, r, question):
    """Store an AgentCore run like a local one and write its trace file - show / trace / approve work unchanged."""
    rid = r.get("run_id") or ""
    if not rid:
        raise SetupError("the runtime returned no run: " + spans.redact(json.dumps(r), limit=None))
    tr = spans.Tracer("capstone", trace_id=rid)
    with tr.path.open("a", encoding="utf-8") as f:
        for s in r.get("trace") or []:
            f.write(json.dumps(s, separators=(",", ":"), ensure_ascii=False) + "\n")
    store.create_run(rid, r.get("account_id", ""), r.get("as_of", ""), question, "agentcore")
    store.finish_run(rid, r.get("status", ""), float(r.get("cost_usd") or 0), int(r.get("turns") or 0),
                     int(r.get("tool_calls") or 0), r.get("error"), str(tr.path))
    if r.get("proposal") is not None:
        store.save_proposal(rid, r["proposal"], r.get("sla"), r.get("verdict"), r.get("trajectory"))
    return tr.path


def eval_target():
    """The agentcore eval target: one invocation per case, graded exactly like local runs."""
    def run(kase):
        t0 = time.monotonic()
        try:
            r = call(kase["account"], kase["as_of"], kase.get("question", ""), "capstone-eval", new_id())
            out = {k: v for k, v in r.items() if k not in ("trace", "sla")}
            out["seconds"] = jround(time.monotonic() - t0, 1)
            st = r.get("status", "")
            if st in ("failed", "guardrail_intervened") or ("error" in r and "proposal" not in r):
                return {"error": r.get("error") or st, "cost_usd": float(r.get("cost_usd") or 0)}
            return out
        except Exception as e:
            return {"error": f"{type(e).__name__}: {spans.redact(str(e), limit=None)}", "cost_usd": 0.0}
    return run


# ----------------------------------------------------------------------------------------------- gate-check

def gate_check():
    """The AgentCore-native half of the approval point: prove at the Gateway that the AGENT's identity (the
    investigator client this runtime uses) cannot write, whatever a prompt says. tools/list shows it no write tool,
    and a direct tools/call ops-write___add_ticket_comment is DENIED by Cedar - nothing is written. The client secret
    is read from Cognito into memory for this one token request and never printed or stored."""
    cid = need("clients.investigator.client_id")
    scope = " ".join(need("clients.investigator.scopes"))
    secret = client("cognito-idp").describe_user_pool_client(UserPoolId=need("user_pool"), ClientId=cid)["UserPoolClient"]["ClientSecret"]
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(need("token_url"), data=urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": scope}).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        token = json.loads(r.read())["access_token"]
    say(f"identity the agent's own client (investigator), scope '{scope}'")
    url = need("gateway_url")
    tools = rpc(url, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = [t.get("name", "") for t in (tools.get("result") or {}).get("tools", [])]
    writes = sum(1 for n in names if n.startswith("ops-write___"))
    say(f"tools    tools/list shows {len(names)} tools, {writes} write tools")
    res = rpc(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "ops-write___add_ticket_comment", "arguments": {
            "ticket_id": "T-1001", "comment": "[capstone gate-check] this call must be denied",
            "Idempotency-Key": str(uuid.uuid4())}}})
    result = res.get("result") or {}
    if "error" in res or result.get("isError"):
        msg = (res.get("error") or {}).get("message") if "error" in res else ((result.get("content") or [{}])[0].get("text", ""))
        say("DENIED   " + str(msg)[:220])
        return 0 if writes == 0 else 1
    say("UNEXPECTED ALLOW - the agent identity could write through the Gateway: " + json.dumps(result)[:300])
    return 1


def rpc(url, token, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, status = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    start, end = raw.find("{"), raw.rfind("}")        # the body may be SSE-framed
    return json.loads(raw[start:end + 1]) if start >= 0 else {"error": {"message": f"HTTP {status}"}}


# ------------------------------------------------------------------------------------------------ teardown

def teardown():
    """Deletes exactly what capstone-state.json lists - the runtime, its log group, its role, its code object.
    Never the shared stack."""
    o = own()
    rt = o.get("runtime") or {}
    if rt.get("id"):
        ac = client("bedrock-agentcore-control")
        try:
            ac.delete_agent_runtime(agentRuntimeId=rt["id"])
            say(f"runtime  deleted {rt.get('name')}")
            for _ in range(30):
                try:
                    ac.get_agent_runtime(agentRuntimeId=rt["id"])
                    time.sleep(5)
                except ac.exceptions.ResourceNotFoundException:
                    break
        except ac.exceptions.ResourceNotFoundException:
            say("runtime  already gone")
        logs = client("logs")
        try:
            logs.delete_log_group(logGroupName=rt.get("log_group", ""))
            say(f"logs     deleted {rt.get('log_group')}")
        except logs.exceptions.ResourceNotFoundException:
            say("logs     no log group")
        save_own("runtime", None)
    if o.get("role_name"):
        iam = client("iam")
        try:
            iam.delete_role_policy(RoleName=o["role_name"], PolicyName="least-privilege")
        except iam.exceptions.NoSuchEntityException:
            pass
        try:
            iam.delete_role(RoleName=o["role_name"])
            say(f"iam      deleted role {o['role_name']}")
        except iam.exceptions.NoSuchEntityException:
            say("iam      role already gone")
        save_own("role_name", None)
    if o.get("code"):
        client("s3").delete_object(Bucket=o["code"]["bucket"], Key=o["code"]["key"])
        say(f"s3       deleted {o['code']['key']}")
        save_own("code", None)
    say("done     nothing of the shared Day 4 stack was touched")
    return 0


# ---------------------------------------------------------------------------------------------------- main

USAGE = """\
python3 agentcore/capstone_aws.py <command>      (AgentCore mode; AC_PREFIX, AWS_REGION, AC_MODEL_ID)
  deploy   [--no-build]                               role + runtime (direct code deploy), reusing the shared stack (read-only)
  invoke   --account ACC --as-of ISO [--question T]   one run on AgentCore, stored and traced locally
  approve  RUN --by NAME --reason WHY                 (reject too) who = your AWS identity + name
  gate-check                                          the agent identity is DENIED a write at the Gateway
  eval     [--repeat N] [--cases a,b] [--budget USD]  the golden set against the deployed runtime
  teardown --yes                                      delete what capstone-state.json lists
  show RUN | list | trace RUN"""


def main(argv):
    if not argv or argv[0].startswith("-h"):
        print(USAGE)
        return 2 if not argv else 0
    cmd, rest = argv[0], argv[1:]
    flags = [a for a in rest if a in ("--yes", "--no-build")]
    db = os.environ.get("CAPSTONE_DB") or cli.default_db()
    try:
        o = cli.Opts([a for a in rest if a not in flags])
        if cmd == "deploy":
            return deploy(flags)
        if cmd == "invoke":
            acc, as_of = o.need("account"), iso(parse_instant(o.need("as-of")))
            t0 = time.monotonic()
            r = call(acc, as_of, o.get("question"), o.get("actor", "duty-manager"), new_id())
            with Store(db) as store:
                trace = record(store, r, o.get("question"))
                print(f"run {r['run_id']} on AgentCore · {jround(time.monotonic() - t0)}s · trace {trace}")
                cli.show(store, r["run_id"], sys.stdout)
            st = r.get("status", "")
            return 3 if st == "blocked" else 1 if st in ("failed", "guardrail_intervened") else 0
        if cmd in ("approve", "reject"):
            principal = client("sts").get_caller_identity()["Arn"]
            with Store(db) as store:
                rid = o.positional[0] if o.positional else ""
                gate.decide(store, store.run(rid).id, cmd, o.get("by"), principal, o.get("reason"),
                            spans.Tracer("capstone", trace_id=rid))
                cli.show(store, rid, sys.stdout)
            return 0
        if cmd == "gate-check":
            return gate_check()
        if cmd == "eval":
            golden = evals.load(o.get("golden", str(repo.solution() / "golden" / "cases.json")))
            return evals.execute(golden, evals.select(golden, o.get("cases")), o.int_or("repeat", 1), o.int_or("workers", 3),
                                 o.float_or("budget", 1.5), "agentcore", eval_target(),
                                 o.get("out", str(repo.python_dir() / "results")), sys.stdout)
        if cmd == "teardown":
            if "--yes" not in flags:
                print("teardown deletes the capstone runtime, its role and its code object: add --yes", file=sys.stderr)
                return 3
            return teardown()
        if cmd in ("show", "list", "trace"):
            return cli.main(argv)
        print(f"unknown command {cmd}\n{USAGE}", file=sys.stderr)
        return 2
    except (GateError, ArgError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"{type(e).__name__}: {spans.redact(str(e), limit=None)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
