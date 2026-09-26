"""Shared settings for every AgentCore step. Nothing here is secret.

Each step records what it created in out/state.json (gitignored), so later steps and
teardown find resources by what was actually created - never by guessing names.

    AC_PREFIX      name prefix for everything you create (default aira-d4). Participants: use your id, e.g. p05
    AWS_REGION     default ap-south-1
    AIRA_OPS_URL   the shared aira-ops API (the trainer gives you this)
"""
import json, os, pathlib, time
import boto3

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
REGION = os.environ.get("AWS_REGION", "ap-south-1")
PREFIX = os.environ.get("AC_PREFIX", "aira-d4")
ACCOUNT = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
MODEL_ID = os.environ.get("AC_MODEL_ID", "global.anthropic.claude-sonnet-5")
STATE = OUT / "state.json"

def client(name, **config):
    """config -> botocore Config, e.g. client("bedrock-agentcore", read_timeout=900, retries={"total_max_attempts": 1})"""
    from botocore.config import Config
    return boto3.client(name, region_name=REGION, config=Config(**config) if config else None)

def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def save(**kw):
    s = state(); s.update(kw)
    STATE.write_text(json.dumps(s, indent=2, default=str)); STATE.chmod(0o600)
    return s

def need(*keys):
    s = state()
    missing = [k for k in keys if k not in s]
    if missing:
        raise SystemExit(f"out/state.json has no {missing} - run the earlier step that creates it first")
    return [s[k] for k in keys]

def say(*a):
    print("  " + " ".join(str(x) for x in a), flush=True)

def wait(fn, ok, bad=("FAILED",), what="resource", tries=90, pause=5):
    """Poll fn() until its status is in ok (or bad). Returns the last response."""
    for _ in range(tries):
        r = fn(); st = r.get("status") or r.get("Status")
        if st in ok or st in bad:
            if st in bad:
                raise SystemExit(f"{what} {st}: {r.get('failureReason') or r.get('statusReasons')}")
            return r
        time.sleep(pause)
    raise SystemExit(f"{what} still not ready")

def role(name, service, policy, extra_trust=None):
    """Create or update an IAM role trusted by one AWS service, with one inline policy."""
    iam = boto3.client("iam")
    stmt = {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}
    stmt.update(extra_trust or {})
    trust = {"Version": "2012-10-17", "Statement": [stmt]}
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        fresh = False
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=f"AgentCore Day 4 ({PREFIX})")["Role"]["Arn"]
        fresh = True
    iam.put_role_policy(RoleName=name, PolicyName="least-privilege", PolicyDocument=json.dumps(policy))
    if fresh:
        time.sleep(12)          # a brand-new role takes a few seconds before services can assume it
    return arn
