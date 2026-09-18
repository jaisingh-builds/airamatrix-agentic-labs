// Lab 1.1 - Build an agent loop from scratch. No framework.
//
// You are writing the loop. That is the whole exercise: everything in the rest
// of the programme sits on top of the cycle you are about to implement.
//
//     observe -> decide -> act -> observe
//
// Run it:   node agent.mjs
// Check it: node --test
//
// This is the worked solution (branch `solutions`). The starter, with the five
// TODOs, is on `main`. Every stop_reason has its own branch: see runAgent below.
import { GatewayClient, Config, Tracer, BudgetGuard, BudgetExceeded }
  from "../../../labkit/node/agentic-core.mjs";
import * as labTools from "./tools.mjs";
import { serveInBackground } from "./fixture-server.mjs";

const SYSTEM =
  "You are an operations assistant. Use the provided tools to gather facts " +
  "before answering. Never guess a number you could compute with the calculator, " +
  "and never invent file contents. When you have the answer, state it plainly.";

export class StepLimitExceeded extends Error {}
/** stop_reason === "max_tokens": the reply was cut off, so it is not an answer. */
export class Truncated extends Error {}
/** stop_reason === "refusal": the model declined to continue. */
export class Refused extends Error {}
/** A stop_reason this code has never seen. Fail safely and keep the trace. */
export class UnhandledStop extends Error {}

export async function runAgent(goal, { maxSteps, verbose = true } = {}) {
  const cfg = new Config();
  const client = new GatewayClient(cfg);
  const tracer = new Tracer("lab1");
  const budget = new BudgetGuard(cfg.budgetUsd, cfg.model);
  const limit = maxSteps || cfg.maxSteps;

  const messages = [{ role: "user", content: goal }];
  tracer.emit("start", { goal, model: cfg.model, max_steps: limit });

  for (let step = 1; step <= limit; step++) {
    budget.check();

    const response = await client.messages({ messages, tools: labTools.SCHEMAS, system: SYSTEM });

    const stop = response.stop_reason;
    const blocks = response.content || [];
    const calls = blocks.filter((b) => b.type === "tool_use");
    const text = blocks.filter((b) => b.type === "text").map((b) => b.text).join("").trim();
    const cost = budget.record(response.usage || {});
    tracer.step(step, stop || "none", text, calls.map((c) => c.name));
    if (verbose) {
      console.log(`  step ${step}: stop=${stop} tools=${calls.map((c) => c.name).join(",") || "-"} ` +
        `($${cost.toFixed(4)}, ${budget.summary()})`);
    }

    // One branch per stop_reason. "Not tool_use" is not a synonym for "done":
    // that is how a truncated or refused reply gets reported as an answer.
    if (stop === "end_turn" || stop === "stop_sequence") {
      if (!text) throw new UnhandledStop(`model ended the turn with no text (stop_reason=${stop})`);
      tracer.emit("finish", { answer: text.slice(0, 400), spend: budget.summary() });
      return text;
    }
    if (stop === "max_tokens") {
      tracer.emit("stopped", { reason: "max_tokens", step });
      throw new Truncated(`reply was cut off at the token limit after ${step} steps. ${budget.summary()}`);
    }
    if (stop === "refusal") {
      tracer.emit("stopped", { reason: "refusal", step });
      throw new Refused(`the model declined to continue. ${budget.summary()}`);
    }
    if (stop === "pause_turn") {
      messages.push({ role: "assistant", content: blocks });   // resume a long-running turn
      continue;
    }
    if (stop !== "tool_use") {
      tracer.emit("stopped", { reason: `unhandled stop_reason=${stop}`, step });
      throw new UnhandledStop(String(stop));
    }

    // The assistant turn goes in BEFORE the results, and every tool_use block
    // gets a matching tool_result in ONE user message.
    messages.push({ role: "assistant", content: blocks });
    const results = [];
    for (const call of calls) {
      const [out, ok] = await labTools.dispatch(call.name, call.input || {});
      tracer.tool(call.name, call.input || {}, out, ok);
      results.push({ type: "tool_result", tool_use_id: call.id, content: out, is_error: !ok });
    }
    messages.push({ role: "user", content: results });
  }

  // Falling out of the loop means the agent never finished. That is the step
  // limit doing its job - an agent without one is a production incident.
  tracer.emit("step_limit", { limit });
  throw new StepLimitExceeded(`agent did not finish within ${limit} steps. ${budget.summary()}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await serveInBackground();
  const goal = process.argv.slice(2).join(" ") ||
    "Fetch the ingest-tier status from http://127.0.0.1:8137/status.json, " +
    "read limits.txt from the workspace, and tell me whether the service is " +
    "over capacity. If it is, compute by what percentage the queue depth " +
    "exceeds the limit, and name the escalation contact.";
  console.log("GOAL:", goal, "\n");
  try {
    console.log("\nANSWER:\n" + await runAgent(goal));
  } catch (err) {
    if (err instanceof StepLimitExceeded || err instanceof BudgetExceeded
        || err instanceof Truncated || err instanceof Refused || err instanceof UnhandledStop) {
      console.log(`\nHALTED: ${err.message}`); process.exit(1);
    }
    throw err;
  }
}
