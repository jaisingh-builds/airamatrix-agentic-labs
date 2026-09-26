"""Step 7b - the HUMAN approver applies the change the agents asked for.

    PYTHONPATH=.. python approve.py --value 16 --version 1     # permitted: approver scope, value <= 16
    PYTHONPATH=.. python approve.py --value 32 --version 1     # denied by the hard_ceiling forbid policy
    PYTHONPATH=.. python approve.py --as supervisor --value 16 --version 1   # an agent's client: denied

The approver's client secret lives only in out/approver.json (written by step 3, mode 0600). The agents'
runtimes never see it - that is the whole point: the approval is a separate identity, enforced at the Gateway.
Take --value and --version from the supervisor's "APPROVAL REQUESTED" comment on the ticket.
"""
import argparse, base64, json, urllib.parse, urllib.request, uuid
from common import OUT, client, need, say

p = argparse.ArgumentParser()
p.add_argument("--value", type=int, required=True)
p.add_argument("--version", type=int, required=True, help="expected_version from the approval request")
p.add_argument("--as", dest="who", default="approver", choices=["approver", "supervisor", "investigator"])
a = p.parse_args()

gw_url, clients, token_url, pool = need("gateway_url", "clients", "token_url", "user_pool")
if a.who == "approver":
    creds = json.loads((OUT / "approver.json").read_text())
    cid, secret, scope = creds["client_id"], creds["client_secret"], creds["scope"]
else:                       # the agent's own client, to show the policy denies it even with valid credentials
    cid, scope = clients[a.who]["client_id"], " ".join(clients[a.who]["scopes"])
    secret = client("cognito-idp").describe_user_pool_client(UserPoolId=pool, ClientId=cid)["UserPoolClient"]["ClientSecret"]

basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
req = urllib.request.Request(token_url, data=urllib.parse.urlencode(
    {"grant_type": "client_credentials", "scope": scope}).encode(),
    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]
say(f"identity {a.who}: token with scope '{scope}'")

args = {"value": a.value, "expected_version": a.version, "Idempotency-Key": str(uuid.uuid4())}
body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ops-write___set_ingest_concurrency", "arguments": args}}
req = urllib.request.Request(gw_url, data=json.dumps(body).encode(), headers={
    "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
raw = urllib.request.urlopen(req, timeout=60).read().decode()
msg = json.loads(raw[raw.index("{"):] if not raw.startswith("{") else raw)
if "error" in msg:
    say("DENIED  ", msg["error"]["message"][:220])
elif msg["result"].get("isError"):
    say("REJECTED by aira-ops:", msg["result"]["content"][0]["text"][:300])
else:
    say("APPLIED ", msg["result"]["content"][0]["text"][:300])
