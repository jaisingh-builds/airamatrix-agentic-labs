// The single agent: a tool-use loop in plain code (Day 1's loop, grown up). Same controls as the Day 4 runners:
//   * a turn limit and a budget cap, both checked BEFORE every model call; a failure reports what it cost
//   * structured output: submit_proposal's schema IS the contract, validated here; two fix-up rounds
//   * refuses to start while a write/admin token is in this process (least privilege per process)
//   * a Bedrock Guardrail intervention (AgentCore mode) ends the run - it is never retried around
// The model is anything with messages(messages, tools, system, maxTokens) returning a Messages-API response:
// labkit's GatewayClient in local mode (localModel below), Bedrock Converse in agentcore/.
import { BudgetExceeded, BudgetGuard, GatewayError } from "../../../../../labkit/node/agentic-core.mjs";
import { ContractError } from "./contracts.mjs";
import { contract, misplaced } from "./guardrails.mjs";
import { cut } from "./sla.mjs";
import fs from "node:fs";
import path from "node:path";
import { redact } from "./spans.mjs";
import { SUBMIT, definitions, err } from "./tools.mjs";

export const FORBIDDEN_ENV = ["AIRA_OPS_APPLY_TOKEN", "AIRA_OPS_TOKEN"];
export const MAX_TOKENS = 3000;
export const CONTRACT_RETRIES = 2;       // as common's GatewayAgentRunner: two fix-up rounds
export const MAX_RESULT_CHARS = 8000;

/** A run that produced no valid proposal. kind: budget | turns | contract | no_result | gateway | guardrail_intervened | forbidden_env */
export class RunError extends Error {
  constructor(kind, message, costUsd, turns, toolCalls) {
    super(message); this.kind = kind; this.costUsd = costUsd; this.turns = turns; this.toolCalls = [...toolCalls];
  }
}

export { GatewayError };

/** labkit's gateway client as a model: .env -> Config -> GatewayClient. Unreachable -> GatewayError(0), like Java labkit. */
export function localModel(gateway) {
  return {
    async messages(messages, tools, system, maxTokens) {
      try {
        return await gateway.messages({ messages, tools, system, maxTokens });
      } catch (e) {
        if (e instanceof GatewayError) throw e;
        throw new GatewayError(0, `cannot reach ${gateway.cfg.baseUrl}: ${e.cause?.code || e.message}`);
      }
    },
  };
}

/** input, plus each required key it lacks copied from prev (in the schema's order). */
export function fillFrom(prev, input, required) {
  const out = structuredClone(input);
  for (const k of required) if (!(k in out) && k in prev) out[k] = structuredClone(prev[k]);
  return out;
}

const NO_TELEMETRY = { tool: (name, args, call) => call() };

export class ResponderAgent {
  /** model: {messages()}; env: a function k -> value (process.env by default); telemetry: agentcore's GenAI spans hook. */
  constructor(model, pricingModel, maxTurns, budgetUsd, env = (k) => process.env[k], telemetry = NO_TELEMETRY) {
    Object.assign(this, { model, pricingModel, maxTurns, budgetUsd, env, telemetry: telemetry || NO_TELEMETRY });
  }

  async run(system, prompt, tools, schema, tr) {
    const calls = [];
    const held = FORBIDDEN_ENV.filter((k) => { const v = this.env(k); return v != null && v !== ""; });
    if (held.length) {    // fail closed, before any cost
      throw new RunError("forbidden_env", `refusing to start the agent: ${held.join(", ")} is set in this process. `
        + "A process that runs the agent holds no write or admin token - run `apply` in its own shell.", 0, 0, calls);
    }
    const toolDefs = definitions(schema);
    const messages = [{ role: "user", content: prompt }];
    const budget = new BudgetGuard(this.budgetUsd, this.pricingModel);
    let contractErrors = 0, nudges = 0;
    let previous = null;              // the last rejected submission: a fix-up is merged onto it

    for (let turn = 1; turn <= this.maxTurns; turn++) {
      const resp = await tr.within("model.turn", { turns: turn }, async (sp) => {
        let r;
        try {
          budget.check();                                       // refuse BEFORE spending
          r = await this.model.messages(messages, toolDefs, system, MAX_TOKENS);
        } catch (e) {
          if (e instanceof BudgetExceeded) {
            sp.fail(e.message);
            throw new RunError("budget", `budget cap reached after ${turn - 1} turns: ${e.message}`, budget.spent, turn - 1, calls);
          }
          if (e instanceof GatewayError) {
            sp.fail(e.message);
            throw new RunError("gateway", `model call failed: HTTP ${e.status} ${redact(String(e.message), 0)}`, budget.spent, turn - 1, calls);
          }
          throw e;
        }
        const cost = budget.record(r.usage || {});
        sp.set("cost_usd", Math.round(cost * 10000) / 10000).set("verdict", r.stop_reason ?? "");
        if (r.stop_reason === "guardrail_intervened") {
          sp.fail("Bedrock Guardrail intervened");
          throw new RunError("guardrail_intervened", `the Bedrock Guardrail intervened on turn ${turn}`
            + " - the run stops; nothing is proposed", budget.spent, turn, calls);
        }
        return r;
      });
      this.debugDump(turn, resp);
      const content = resp.content || [];
      messages.push({ role: "assistant", content });
      if (resp.stop_reason === "max_tokens") {
        // Found live: a reply cut off at max_tokens carried a submit_proposal with only 'summary'. A truncated tool call
        // is never validated or run - every tool_use in it gets an error result, and the model is told why.
        const cutOff = content.filter((b) => b.type === "tool_use").map((b) => ({ type: "tool_result", tool_use_id: b.id, is_error: true,
          content: `your reply was cut off at ${MAX_TOKENS} tokens, so this call was not run. Be brief (summary under 800 characters) `
            + `and call ${SUBMIT} again with all six keys.` }));
        tr.event("contract.rejected", { reason: "reply cut off at max_tokens - tool calls not run" });
        if (++contractErrors > CONTRACT_RETRIES) {
          throw new RunError("contract", `no proposal matching the contract after ${contractErrors} attempts`, budget.spent, turn, calls);
        }
        messages.push({ role: "user", content: cutOff.length ? cutOff : `Your reply was cut off. Be brief and call ${SUBMIT}.` });
        continue;
      }

      const results = [];
      let submitted = null;
      for (const block of content) {
        if (block.type !== "tool_use") continue;
        const name = block.name, input = block.input ?? {};
        const r = { type: "tool_result", tool_use_id: block.id };
        results.push(r);
        if (name === SUBMIT) {
          // Found live: after "missing ['evidence']" the model often resends ONLY the missing key. A fix-up takes the
          // required top-level keys it leaves out from the previous rejected submission, and is validated in full -
          // transport, not trust. Keys the schema does not allow are never carried forward (found live too: with a plain
          // merge an unexpected key sent once could never be removed, and the run failed on it three times).
          const isObj = input && typeof input === "object" && !Array.isArray(input);
          const checked = previous && isObj ? fillFrom(previous, input, schema.required || []) : input;
          try {
            contract(checked, schema);
            submitted = checked;
            r.content = "received";
          } catch (e) {
            if (!(e instanceof ContractError)) throw e;
            if (checked && typeof checked === "object" && !Array.isArray(checked)) previous = structuredClone(checked);
            contractErrors++;
            const sent = isObj ? Object.keys(input) : [];                                   // key names only - never the content
            const missing = (schema.required || []).filter((k) => !(checked && typeof checked === "object" && k in checked));
            tr.event("contract.rejected", { reason: cut(e.message, 200), kept: sent, dropped: misplaced(checked, missing) });
            r.is_error = true;
            r.content = `contract error: ${e.message} - call ${SUBMIT} again with the corrected keys (the keys you already sent are kept).`;
          }
          continue;
        }
        const res = await tr.within("tool", { tool: name, input }, async (ts) => {
          const x = await this.telemetry.tool(name, JSON.stringify(input), () => tools.call(name, input));
          ts.set("ok", !x.error);
          if (x.error) ts.fail(cut(x.text, 200));
          return x;
        });
        calls.push({ name, input, ok: !res.error });
        r.content = res.text.length > MAX_RESULT_CHARS
          ? err("too_large", `result over ${MAX_RESULT_CHARS} chars - refused, not truncated`).text : res.text;
        if (res.error) r.is_error = true;
      }
      if (submitted) return { proposal: submitted, costUsd: budget.spent, turns: turn, toolCalls: calls };
      if (contractErrors > CONTRACT_RETRIES) {
        throw new RunError("contract", `no proposal matching the contract after ${contractErrors} attempts`, budget.spent, turn, calls);
      }
      if (results.length) { messages.push({ role: "user", content: results }); continue; }
      if (++nudges > 1) throw new RunError("no_result", `the agent answered in text instead of calling ${SUBMIT}`, budget.spent, turn, calls);
      messages.push({ role: "user", content: `Call ${SUBMIT} now with your proposal.` });
    }
    throw new RunError("turns", `turn limit ${this.maxTurns} reached without a proposal`, budget.spent, this.maxTurns, calls);
  }

  /** CAPSTONE_DEBUG_DIR: raw model replies, one file per turn, for when the trace is not enough. Holds customer text:
   *  local only, never committed, delete it when done. Off by default. */
  debugDump(turn, resp) {
    const dir = this.env("CAPSTONE_DEBUG_DIR");
    if (!dir || !dir.trim()) return;
    try {
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${Date.now()}-turn${turn}.json`), redact(JSON.stringify(resp), 0));
    } catch { /* debugging aid only */ }
  }
}
