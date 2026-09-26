// One run, the same in both modes: agent -> guardrail (against the SLA recomputed from source) -> a status that
// says whether a human has something to decide. Used by the local CLI, the eval harness and the AgentCore
// runtime's /invocations.
//
//   failed | guardrail_intervened   the agent produced no proposal (budget, turns, contract, Bedrock Guardrail)
//   blocked                         the code guardrail refused the proposal - a human cannot approve it
//   no_action                       nothing to post (nothing exposed, or no exposed ticket)
//   awaiting_approval               a customer update is waiting for a named human with a reason
import { RunError } from "./agent.mjs";
import { SCHEMA, verify } from "./guardrails.mjs";
import { SYSTEM, task } from "./prompts.mjs";
import { compute } from "./sla.mjs";
import { Tools } from "./tools.mjs";

export const round4 = (d) => Math.round(d * 10000) / 10000;

export class Outcome {
  constructor(o) { Object.assign(this, o); }   // runId, status, proposal, verdict, sla, toolCalls, costUsd, turns, error

  trajectory() { return this.toolCalls.map((c) => [c.name, c.input, c.ok]); }

  toJson() {
    const o = { run_id: this.runId, status: this.status, cost_usd: round4(this.costUsd), turns: this.turns, tool_calls: this.toolCalls.length };
    if (this.proposal) o.proposal = this.proposal;
    if (this.verdict) o.verdict = this.verdict.toJson();
    if (this.sla) o.sla = this.sla.toJson();
    o.trajectory = this.trajectory();
    if (this.error != null) o.error = this.error;
    return o;
  }
}

export async function run(runId, accountId, asOf, question, ops, agent, tr) {
  return tr.within("run", { account: accountId, stage: "sla-responder" }, async (root) => {
    let r;
    try {
      r = await agent.run(SYSTEM, task(accountId, asOf.toString(), question), new Tools(ops, accountId, asOf), SCHEMA, tr);
    } catch (e) {
      if (!(e instanceof RunError)) throw e;
      const status = e.kind === "guardrail_intervened" ? "guardrail_intervened" : "failed";
      root.set("cost_usd", round4(e.costUsd)).set("turns", e.turns).set("verdict", status);
      root.fail(`${e.kind}: ${e.message}`);
      return new Outcome({ runId, status, proposal: null, verdict: null, sla: null, toolCalls: e.toolCalls, costUsd: e.costUsd,
        turns: e.turns, error: `${e.kind}: ${e.message}` });
    }
    root.set("cost_usd", round4(r.costUsd)).set("turns", r.turns).set("tool_calls", r.toolCalls.length);

    let sla, v;
    const early = await tr.within("guardrail.verify", { stage: "code-guardrail" }, async (vs) => {
      try {
        sla = await compute(ops, accountId, asOf);          // source of truth, recomputed - not what the agent saw
      } catch (e) {                                         // cannot verify -> fail closed
        vs.fail(e.message);
        root.fail(`verification could not run: ${e.message}`);
        return new Outcome({ runId, status: "failed", proposal: r.proposal, verdict: null, sla: null, toolCalls: r.toolCalls,
          costUsd: r.costUsd, turns: r.turns, error: `verification could not run: ${e.message}` });
      }
      v = verify(r.proposal, sla);
      vs.set("verdict", v.passed ? "pass" : "block").set("denials", v.rules()).set("kept", sla.exposed().length);
      if (!v.passed) vs.fail(`guardrail refused: ${v.rules().join(", ")}`);
      return null;
    });
    if (early) return early;
    const action = r.proposal.action.type;
    const status = !v.passed ? "blocked" : action === "none" ? "no_action" : "awaiting_approval";
    root.set("verdict", status).set("action", action);
    if (status === "awaiting_approval") {
      tr.event("gate.waiting", { action, input: { ticket_id: r.proposal.action.ticket_id } });
    }
    return new Outcome({ runId, status, proposal: r.proposal, verdict: v, sla, toolCalls: r.toolCalls, costUsd: r.costUsd, turns: r.turns, error: null });
  });
}

/** Persist an outcome (local runs, AgentCore invocations, replays). */
export function save(store, o, trace) {
  store.finishRun(o.runId, o.status, o.costUsd, o.turns, o.toolCalls.length, o.error, trace);
  if (o.proposal) store.saveProposal(o.runId, o.proposal, o.sla ? o.sla.toJson() : null, o.verdict ? o.verdict.toJson() : null, o.trajectory());
}
