// The AgentCore Runtime HTTP contract on 0.0.0.0:8080, with node:http: GET /ping and POST /invocations (any content
// type - InvokeAgentRuntime callers may send application/octet-stream). Payload:
// {"prompt", "actor_id", "account_id", "as_of", "run_id"?}. The caller binds the tenant and the clock; the model never
// chooses them. Session id: X-Amzn-Bedrock-AgentCore-Runtime-Session-Id. Workload token (present only when the caller
// passed runtimeUserId): X-Amz-Bedrock-AgentCore-Identity-WAT.
import crypto from "node:crypto";
import http from "node:http";
import { BedrockAgentCoreClient } from "@aws-sdk/client-bedrock-agentcore";
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";
import { redact } from "../../lib/spans.mjs";
import { BedrockConverseModel } from "./bedrock-model.mjs";
import { GatewayOpsReader } from "./gateway-reader.mjs";
import { IdentityError, IdentityTokens } from "./identity.mjs";
import { BadRequest, InvocationService, settings } from "./invocation.mjs";

export const SESSION = "x-amzn-bedrock-agentcore-runtime-session-id";
export const WAT = "x-amz-bedrock-agentcore-identity-wat";
const MAX_BODY = 64 * 1024;

/** Production wiring: the AWS SDK clients, the MCP Gateway reader, Bedrock with the guardrail. */
export function wire(env = process.env) {
  const cfg = settings(env);
  const bedrock = new BedrockRuntimeClient({ region: cfg.region, maxAttempts: 3 });
  const identity = new IdentityTokens(new BedrockAgentCoreClient({ region: cfg.region }), cfg.oauthProvider, cfg.oauthScopes);
  return new InvocationService(cfg, identity, (url, token) => GatewayOpsReader.open(url, token),
    (tel) => new BedrockConverseModel((req) => bedrock.send(new ConverseCommand(req)), cfg.modelId, cfg.guardrailId, cfg.guardrailVersion, tel));
}

const send = (res, status, obj) => {
  const body = JSON.stringify(obj);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  res.end(body);
};

/** The request handler, separate from listen() so tests drive it with a fake service. */
export function handler(getService) {
  return async (req, res) => {
    const url = (req.url || "").split("?")[0];
    if (req.method === "GET" && url === "/ping") return send(res, 200, { status: "Healthy", time_of_last_update: Math.floor(Date.now() / 1000) });
    if (req.method !== "POST" || url !== "/invocations") return send(res, 404, { error: `no route ${req.method} ${url}` });
    const chunks = [];
    let n = 0;
    for await (const c of req) {
      n += c.length;
      if (n > MAX_BODY) return send(res, 400, { error: "body over 64 KiB" });
      chunks.push(c);
    }
    let payload;
    try {
      const raw = Buffer.concat(chunks).toString("utf8");
      payload = raw.trim() ? JSON.parse(raw) : {};
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("not an object");
    } catch {
      return send(res, 400, { error: "body must be a JSON object" });
    }
    try {
      const out = await getService().invoke(payload, req.headers[SESSION] || crypto.randomUUID(), req.headers[WAT] || req.headers.workloadaccesstoken);
      return send(res, 200, out);
    } catch (e) {
      if (e instanceof BadRequest || e instanceof IdentityError) return send(res, 400, { error: redact(String(e.message), 0) });
      console.error(`invocation failed: ${redact(String(e.stack || e), 0)}`);
      return send(res, 500, { error: `${e.constructor?.name || "Error"}: ${redact(String(e.message), 0)}` });
    }
  };
}

export function start(port = Number(process.env.PORT || 8080)) {
  let service;
  const server = http.createServer(handler(() => (service ??= wire())));
  server.requestTimeout = 0;           // a run can take minutes; the Runtime owns the timeout
  server.listen(port, "0.0.0.0", () => console.log(`aira-capstone responder listening on 0.0.0.0:${port}`));
  return server;
}
