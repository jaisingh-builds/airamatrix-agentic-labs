// AgentCore Identity. The Runtime gives each request a workload access token (only when the caller passed
// runtimeUserId); the token vault exchanges it for an OAuth token from the credential provider named at deploy
// time - the INVESTIGATOR's, whose scope is read-only, so Cedar shows this agent only ops-read tools. No client
// secret reaches this code or the package.
// Every request must bring its own workload token, checked BEFORE the cache, and a cached Gateway token is reused
// only for the SAME workload token - a cache checked first would let a request with no token, or with another
// caller's, act with the previous caller's identity for up to 50 minutes.
import { createHash } from "node:crypto";
import { GetResourceOauth2TokenCommand } from "@aws-sdk/client-bedrock-agentcore";

export class IdentityError extends Error {}

export class IdentityTokens {
  /** client: a BedrockAgentCoreClient (or anything with send()). */
  constructor(client, provider, scopes) {
    Object.assign(this, { client, provider, scopes, cached: null, cachedFor: null, expires: 0 });
  }

  async gatewayToken(workloadAccessToken) {
    if (!workloadAccessToken || !workloadAccessToken.trim()) {
      throw new IdentityError("no workload access token on this request - invoke the runtime with runtimeUserId");
    }
    const key = createHash("sha256").update(workloadAccessToken).digest("hex");    // the token itself is not kept
    if (this.cached && this.cachedFor === key && Date.now() < this.expires) return this.cached;
    const r = await this.client.send(new GetResourceOauth2TokenCommand({
      workloadIdentityToken: workloadAccessToken, resourceCredentialProviderName: this.provider, scopes: this.scopes, oauth2Flow: "M2M",
    }));
    if (!r.accessToken) throw new IdentityError(`token vault returned no access token for ${this.provider}`);
    this.cached = r.accessToken;
    this.cachedFor = key;
    this.expires = Date.now() + 50 * 60 * 1000;
    return this.cached;
  }
}
