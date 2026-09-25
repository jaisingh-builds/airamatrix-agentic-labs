#!/usr/bin/env python3
"""Step 3 - identities: who each agent is, and what it may ask for.

  Cognito user pool  {PREFIX}-agents, resource server "aira-ops" with three scopes:
      aira-ops/read      read tickets, config, accounts, the handbook
      aira-ops/comment   post a ticket comment
      aira-ops/config    change production configuration
  One OAuth client per ROLE (client-credentials, 1-hour tokens):
      investigator  read            reviewer  read
      supervisor    read comment    approver  config      <- a human's client, never an agent's
  One AgentCore Identity OAuth2 credential provider per AGENT role. The agent code asks the
  token vault for a token (@requires_access_token, M2M): no client secret is ever in the code,
  the zip, or the environment of the runtime.

The approver's client secret is written to out/approver.json (0600) - the human uses it in 07-run.
"""
import json
from common import ACCOUNT, PREFIX, REGION, OUT, client, save, say, state

cog, ac = client("cognito-idp"), client("bedrock-agentcore-control")
POOL = f"{PREFIX}-agents"
ROLES = {"investigator": ["read"], "reviewer": ["read"], "supervisor": ["read", "comment"], "approver": ["config"]}

pools = [p for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == POOL]
pool = pools[0]["Id"] if pools else cog.create_user_pool(PoolName=POOL)["UserPool"]["Id"]
domain = f"{PREFIX}-agents-{ACCOUNT}"
if not cog.describe_user_pool(UserPoolId=pool)["UserPool"].get("Domain"):
    cog.create_user_pool_domain(Domain=domain, UserPoolId=pool)
try:
    cog.describe_resource_server(UserPoolId=pool, Identifier="aira-ops")
except cog.exceptions.ResourceNotFoundException:
    cog.create_resource_server(UserPoolId=pool, Identifier="aira-ops", Name="aira-ops", Scopes=[
        {"ScopeName": "read", "ScopeDescription": "Read tickets, config, accounts, handbook"},
        {"ScopeName": "comment", "ScopeDescription": "Post a ticket comment"},
        {"ScopeName": "config", "ScopeDescription": "Change production configuration"}])
discovery = f"https://cognito-idp.{REGION}.amazonaws.com/{pool}/.well-known/openid-configuration"
token_url = f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token"

existing = {c["ClientName"]: c["ClientId"] for c in cog.list_user_pool_clients(UserPoolId=pool, MaxResults=60)["UserPoolClients"]}
clients = {}
for role, scopes in ROLES.items():
    name = f"{PREFIX}-{role}"
    kw = dict(AllowedOAuthFlows=["client_credentials"], AllowedOAuthScopes=[f"aira-ops/{s}" for s in scopes],
              AllowedOAuthFlowsUserPoolClient=True, AccessTokenValidity=1, TokenValidityUnits={"AccessToken": "hours"})
    if name in existing:
        cid = existing[name]; cog.update_user_pool_client(UserPoolId=pool, ClientId=cid, ClientName=name, **kw)
    else:
        cid = cog.create_user_pool_client(UserPoolId=pool, ClientName=name, GenerateSecret=True, **kw)["UserPoolClient"]["ClientId"]
    secret = cog.describe_user_pool_client(UserPoolId=pool, ClientId=cid)["UserPoolClient"]["ClientSecret"]
    clients[role] = {"client_id": cid, "scopes": [f"aira-ops/{s}" for s in scopes], "secret": secret}
    say(f"client {role:<12} {cid}  scopes={' '.join(clients[role]['scopes'])}")

# Agents get their tokens from the AgentCore Identity token vault - one provider per agent role.
providers = {}
have = {p["name"]: p for p in ac.list_oauth2_credential_providers(maxResults=20).get("credentialProviders", [])}
for role in ("investigator", "reviewer", "supervisor"):
    name = f"{PREFIX}-{role}-oauth"
    cfg = {"customOauth2ProviderConfig": {"oauthDiscovery": {"discoveryUrl": discovery},
           "clientId": clients[role]["client_id"], "clientSecret": clients[role]["secret"]}}
    if name in have:
        r = ac.update_oauth2_credential_provider(name=name, credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput=cfg)
    else:
        r = ac.create_oauth2_credential_provider(name=name, credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput=cfg)
    providers[role] = {"name": name, "arn": r["credentialProviderArn"], "secret_arn": r["clientSecretArn"]["secretArn"]}
    say(f"token vault  {name}")

ap = OUT / "approver.json"
ap.write_text(json.dumps({"client_id": clients["approver"]["client_id"], "client_secret": clients["approver"]["secret"],
                          "token_url": token_url, "scope": "aira-ops/config"}, indent=2)); ap.chmod(0o600)
save(user_pool=pool, discovery_url=discovery, token_url=token_url,
     clients={r: {k: v for k, v in c.items() if k != "secret"} for r, c in clients.items()}, providers=providers)
say("wrote out/approver.json (the human approver's credential - keep it off agents)")
