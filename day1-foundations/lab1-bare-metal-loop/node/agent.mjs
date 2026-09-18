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
// Five TODOs. Work top to bottom. The reference solution is on the `solutions`
// branch - try each TODO before you look.
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

    // -------------------------------------------------------------- TODO 1
    // Call the model. Pass the conversation so far, labTools.SCHEMAS and SYSTEM.
    // See GatewayClient.messages() for the argument shape.
    //
    //   const response = await client.messages({ ... });
    throw new Error("TODO 1: call the model");

    // -------------------------------------------------------------- TODO 2
    // Pull out what you need:
    //   stop   - response.stop_reason
    //   blocks - response.content            (array of content blocks)
    //   calls  - blocks where type === "tool_use"
    // Record the cost: budget.record(response.usage || {})

    // -------------------------------------------------------------- TODO 3
    // Branch on stop. There is NO single "not tool_use means done" branch -
    // that is how an agent reports a truncated or refused reply as an answer:
    //
    //   "tool_use"                  -> fall through to TODO 4
    //   "end_turn" | "stop_sequence"-> join the text blocks, check it is not
    //                                  empty, trace it and return it
    //   "max_tokens"                -> throw new Truncated(...) - the reply was
    //                                  cut off, so it is not the answer
    //   "refusal"                   -> throw new Refused(...)
    //   "pause_turn"                -> send the conversation back unchanged to
    //                                  continue; not finished
    //   anything else               -> throw new UnhandledStop(stop)

    // -------------------------------------------------------------- TODO 4
    // Otherwise the model wants tools. Two rules that are easy to get wrong:
    //   a) push the assistant's blocks onto messages BEFORE the results
    //   b) EVERY tool_use block needs a matching tool_result, and they all go
    //      back in ONE user message. Splitting them quietly teaches the model
    //      to stop calling tools in parallel.
    //
    //   const [out, ok] = await labTools.dispatch(call.name, call.input);
    //   { type: "tool_result", tool_use_id: call.id, content: out, is_error: !ok }

    // -------------------------------------------------------------- TODO 5
    // Push the results as a single { role: "user", content: results } message
    // and let the loop go round again.
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
