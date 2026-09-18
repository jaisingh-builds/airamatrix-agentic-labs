// Lab 1.1 loop contract (Node) - offline, no model, no spend.
// These fail until TODO 3 is finished: they are the specification for
// "the agent finished" versus "the agent stopped".
import { test } from "node:test";
import assert from "node:assert/strict";
import * as core from "../../../labkit/node/agentic-core.mjs";
import { runAgent, StepLimitExceeded, Truncated, Refused, UnhandledStop } from "./agent.mjs";

function reply(stop_reason, { text = "", tool = null } = {}) {
  const content = [];
  if (tool) content.push({ type: "tool_use", id: "t1", name: tool[0], input: tool[1] });
  if (text) content.push({ type: "text", text });
  return { stop_reason, content, usage: { input_tokens: 100, output_tokens: 20 } };
}

/** Swap the gateway for a scripted client, run, and always put it back. */
async function runWith(replies, options = {}) {
  const original = core.GatewayClient.prototype.messages;
  let i = 0;
  core.GatewayClient.prototype.messages = async () => replies[Math.min(i++, replies.length - 1)];
  try {
    return await runAgent("goal", { verbose: false, ...options });
  } finally {
    core.GatewayClient.prototype.messages = original;
  }
}

test("end_turn returns the answer", async () => {
  assert.match(await runWith([reply("end_turn", { text: "The overage is 62.4%." })]), /62\.4/);
});

test("end_turn with no text is not an answer", async () => {
  await assert.rejects(() => runWith([reply("end_turn")]), UnhandledStop);
});

test("max_tokens is not an answer", async () => {
  await assert.rejects(() => runWith([reply("max_tokens", { text: "The overage is 6" })]), Truncated);
});

test("refusal is not an answer", async () => {
  await assert.rejects(() => runWith([reply("refusal")]), Refused);
});

test("an unknown stop_reason fails safely", async () => {
  await assert.rejects(() => runWith([reply("some_stop_reason_from_2027")]), UnhandledStop);
});

test("tool_use then end_turn completes", async () => {
  const answer = await runWith([
    reply("tool_use", { tool: ["read_file", { path: "limits.txt" }] }),
    reply("end_turn", { text: "max_queue_depth is 500." }),
  ]);
  assert.match(answer, /500/);
});

test("the step limit halts the run", async () => {
  await assert.rejects(
    () => runWith([reply("tool_use", { tool: ["read_file", { path: "limits.txt" }] })], { maxSteps: 3 }),
    StepLimitExceeded);
});
