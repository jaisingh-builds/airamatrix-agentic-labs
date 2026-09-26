// One invocation = one run of the SAME responder as local mode (lib/responder.mjs): identity -> Gateway reads ->
// Bedrock (guardrail on) -> code guardrail -> status. Returns the run record plus its JSONL trace records, so the
// caller keeps one trace format for both modes. Nothing here can write: no write tool, no write token.
import fs from "node:fs";
import { ResponderAgent } from "../../lib/agent.mjs";
import * as responder from "../../lib/responder.mjs";
import { Tracer } from "../../lib/spans.mjs";
import { newId } from "../../lib/store.mjs";
import { parseInstant } from "../../lib/time.mjs";
import { GenAiTelemetry } from "./telemetry.mjs";

const ACCOUNT = /^ACC-\d{4}$/;

/** A request the caller must fix: HTTP 400 {"error": message}. */
export class BadRequest extends Error {}

/** What the runtime is configured with - environment variables the deploy step sets, never secrets. */
export function settings(env = process.env) {
  const need = (k) => {
    if (!env[k] || !env[k].trim()) throw new BadRequest(`${k} is not set - the deploy step sets it`);
    return env[k];
  };
  return { region: env.AWS_REGION || "ap-south-1", gatewayUrl: need("GATEWAY_URL"), oauthProvider: need("OAUTH_PROVIDER"),
    oauthScopes: need("OAUTH_SCOPES").trim().split(/\s+/), modelId: need("MODEL_ID"), guardrailId: need("GUARDRAIL_ID"),
    guardrailVersion: need("GUARDRAIL_VERSION"), maxBudgetUsd: Number(env.MAX_BUDGET_USD || "0.40"), maxTurns: Number(env.MAX_TURNS || "10") };
}

export class InvocationService {
  /**
   * identity: {gatewayToken(wat)}; openReads: async (gatewayUrl, bearer) -> OpsReader with close();
   * model: (telemetry) -> {messages()}. Production wiring is in server.mjs; tests pass fakes.
   */
  constructor(cfg, identity, openReads, model) { Object.assign(this, { cfg, identity, openReads, model }); }

  async invoke(payload, session, workloadToken) {
    const account = String(payload.account_id ?? ""), asOfText = String(payload.as_of ?? "");
    if (!ACCOUNT.test(account) || !asOfText.trim()) throw new BadRequest("account_id and as_of are required");
    let asOf;
    try { asOf = parseInstant(asOfText); } catch (e) { throw new BadRequest(e.message); }
    const prompt = String(payload.prompt ?? "");
    if (prompt.length > 2000) throw new BadRequest("prompt is over 2000 characters");
    const rid = /^[0-9a-f]{10}$/.test(String(payload.run_id ?? "")) ? String(payload.run_id) : newId();

    const tel = new GenAiTelemetry(session);
    const tr = new Tracer("capstone", rid);
    const agentSpan = tel.agent(prompt);
    let o;
    try {
      o = await tel.within(agentSpan, async () => {
        const token = await this.identity.gatewayToken(workloadToken);
        const reads = await this.openReads(this.cfg.gatewayUrl, token);
        try {
          const agent = new ResponderAgent(this.model(tel), "claude-sonnet", this.cfg.maxTurns, this.cfg.maxBudgetUsd, (k) => process.env[k], tel);
          return await responder.run(rid, account, asOf, prompt, reads, agent, tr);
        } finally {
          await reads.close?.();
        }
      });
      agentSpan.setAttribute("gen_ai.task.output", o.status + (o.proposal ? ` ${o.proposal.summary}` : ""));
    } catch (e) {
      GenAiTelemetry.fail(agentSpan, e);
      throw e;
    } finally {
      agentSpan.end();
    }
    const out = { ...o.toJson(), mode: "agentcore", account_id: account, as_of: asOf.toString(), trace: tr.records };
    try { fs.rmSync(tr.path, { force: true }); } catch { /* /tmp is the microVM's own */ }
    return out;
  }
}
