// Lab 1.1 checks (Node). Offline checks always run.
// Live model checks cost about half a cent and run only with LAB_LIVE=1.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as labTools from "./tools.mjs";
import { serveInBackground } from "./fixture-server.mjs";

test("read_file returns contents", async () => {
  const [out, ok] = await labTools.dispatch("read_file", { path: "limits.txt" });
  assert.ok(ok); assert.match(out, /max_queue_depth/);
});

test("read_file error is recoverable", async () => {
  const [out, ok] = await labTools.dispatch("read_file", { path: "nope.txt" });
  assert.equal(ok, false);
  assert.match(out, /Available files/, "error must tell the agent how to recover");
});

test("read_file cannot escape the workspace", async () => {
  const [, ok] = await labTools.dispatch("read_file", { path: "../../../etc/passwd" });
  assert.equal(ok, false);
});

test("calculator computes", async () => {
  assert.deepEqual(await labTools.dispatch("calculator", { expression: "(812-500)/500*100" }),
                   ["62.4", true]);
});

test("calculator rejects code", async () => {
  const [, ok] = await labTools.dispatch("calculator", { expression: "process.exit(1)" });
  assert.equal(ok, false);
});

test("http_get blocks other hosts", async () => {
  const [out, ok] = await labTools.dispatch("http_get", { url: "http://example.com/" });
  assert.equal(ok, false); assert.match(out, /not allowed/);
});

test("unknown tool is reported", async () => {
  const [, ok] = await labTools.dispatch("definitely_not_a_tool", {});
  assert.equal(ok, false);
});

test("every schema is well-formed", () => {
  for (const schema of labTools.SCHEMAS) {
    assert.ok(schema.name);
    assert.ok(schema.description.length > 40,
      `${schema.name}: description is the prompt - make it count`);
    assert.equal(schema.input_schema.type, "object");
    for (const prop of Object.values(schema.input_schema.properties)) {
      assert.ok(prop.description, "every parameter needs a description");
    }
  }
});

const live = process.env.LAB_LIVE === "1";

test("agent answers the capacity question", { skip: !live && "set LAB_LIVE=1" }, async () => {
  await serveInBackground();
  const { runAgent } = await import("./agent.mjs");
  const answer = await runAgent(
    "Fetch http://127.0.0.1:8137/status.json, read limits.txt, and say whether " +
    "the service is over capacity and by what percentage.", { verbose: false });
  assert.match(answer.replace(/%/g, ""), /62\.4/, "should compute 62.4% via the calculator");
});

test("step limit halts before finishing", { skip: !live && "set LAB_LIVE=1" }, async () => {
  await serveInBackground();
  const { runAgent, StepLimitExceeded } = await import("./agent.mjs");
  await assert.rejects(
    () => runAgent("Read limits.txt and tell me the max_queue_depth value.",
                   { maxSteps: 1, verbose: false }),
    StepLimitExceeded);
});
