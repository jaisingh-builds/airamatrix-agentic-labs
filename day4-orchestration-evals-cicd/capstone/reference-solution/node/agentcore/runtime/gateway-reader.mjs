// AgentCore mode: the reads come from the AgentCore Gateway's ops-read tools over MCP (the official MCP TypeScript
// SDK, streamable HTTP), with this runtime's Identity token. Cedar decides which tools exist for this identity: with
// the investigator's read scope, tools/list has no write tool at all. The Gateway's own aira-ops credential can read
// EVERY account, so the tenant boundary is enforced in lib/ (sla.mjs, tools.mjs), not assumed from the credential.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { OpsError } from "../../lib/ops.mjs";

export const P = "ops-read___";

/** Turn one MCP tools/call result into the aira-ops JSON body, or an OpsError the tools report to the model. */
export function parseResult(tool, r) {
  const text = (r?.content || []).map((c) => (c.type === "text" ? c.text : "")).join("");
  let body;
  try { body = text.trim() ? JSON.parse(text) : {}; } catch { throw new OpsError(502, "invalid", `${P}${tool} returned non-JSON`); }
  const err = body && typeof body === "object" ? body.error : null;
  if (r?.isError === true || (err && typeof err === "object")) {
    const code = err?.code || "tool_error";
    throw new OpsError(code === "not_found" ? 404 : 502, code, err?.message || text.slice(0, 200));
  }
  return body;
}

export class GatewayOpsReader {
  static async open(gatewayUrl, bearerToken, timeoutMs = 30000) {
    const client = new Client({ name: "aira-capstone-node", version: "1.0.0" });
    const transport = new StreamableHTTPClientTransport(new URL(gatewayUrl), {
      requestInit: { headers: { Authorization: `Bearer ${bearerToken}` } },
    });
    await client.connect(transport, { timeout: timeoutMs });
    return new GatewayOpsReader(client, timeoutMs);
  }

  constructor(client, timeoutMs = 30000) { this.client = client; this.timeoutMs = timeoutMs; }

  account(id) { return this.call("lookup_account", { account_id: id }); }
  tickets(accountId) { return this.call("search_tickets", { account_id: accountId, limit: 50 }); }
  ticket(id) { return this.call("get_ticket", { ticket_id: id }); }
  jobs(accountId) { return this.call("list_jobs", { account_id: accountId }); }
  config(key) { return this.call("get_config", { key }); }

  async call(tool, args) {
    let r;
    try {
      r = await this.client.callTool({ name: P + tool, arguments: args }, undefined, { timeout: this.timeoutMs });
    } catch (e) {        // a Cedar denial arrives as a JSON-RPC error
      throw new OpsError(403, "denied", `gateway refused ${P}${tool}: ${e.message}`);
    }
    return parseResult(tool, r);
  }

  async close() { try { await this.client.close(); } catch { /* closing */ } }
}
