// AgentCore mode, offline: no AWS, no model. Needs `npm install` in agentcore/ and agentcore/runtime/ (skipped otherwise).
//   node --test day4-orchestration-evals-cicd/capstone/reference-solution/node/agentcore/tests
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ready = fs.existsSync(path.join(HERE, "../node_modules")) && fs.existsSync(path.join(HERE, "../runtime/node_modules"));
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "capstone-ac-test-"));
process.env.LAB_TRACE_DIR = path.join(TMP, "traces");
for (const k of ["AIRA_OPS_TOKEN", "AIRA_OPS_APPLY_TOKEN"]) delete process.env[k];   // the agent refuses to start with them (by design)

describe("AgentCore mode (offline)", { skip: !ready && "run npm install in agentcore/ and agentcore/runtime/ first" }, () => {
  let m;
  before(async () => {
    m = {
      model: await import("../runtime/bedrock-model.mjs"),
      gw: await import("../runtime/gateway-reader.mjs"),
      inv: await import("../runtime/invocation.mjs"),
      server: await import("../runtime/server.mjs"),
      aws: await import("../aws.mjs"),
      zip: await import("../zip.mjs"),
    };
  });

  test("Messages API <-> Converse, and the guardrail is on every call", async () => {
    const seen = [];
    const converse = async (req) => { seen.push(req); return { stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 5 },
      output: { message: { content: [{ text: "checking" }, { toolUse: { toolUseId: "t1", name: "sla_report", input: {} } }] } } }; };
    const model = new m.model.BedrockConverseModel(converse, "model-x", "gr-1", "3", null);
    const out = await model.messages([{ role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "tool_use", id: "t0", name: "get_ticket", input: { ticket_id: "T-1001" } }] },
      { role: "user", content: [{ type: "tool_result", tool_use_id: "t0", content: "{}", is_error: true }] }],
    [{ name: "sla_report", description: "d", input_schema: { type: "object", properties: {} } }], "sys", 3000);
    assert.deepEqual(seen[0].guardrailConfig, { guardrailIdentifier: "gr-1", guardrailVersion: "3", trace: "enabled" });
    assert.equal(seen[0].inferenceConfig.maxTokens, 3000);
    assert.equal(seen[0].messages[2].content[0].toolResult.status, "error");
    assert.deepEqual(out, { content: [{ type: "text", text: "checking" }, { type: "tool_use", id: "t1", name: "sla_report", input: {} }],
      stop_reason: "tool_use", usage: { input_tokens: 10, output_tokens: 5 } });
    const failing = new m.model.BedrockConverseModel(async () => { throw Object.assign(new Error("slow down"), { name: "ThrottlingException", $metadata: { httpStatusCode: 429 } }); }, "x", "g", "1", null);
    await assert.rejects(failing.messages([{ role: "user", content: "x" }], [], "s", 10), (e) => e.status === 429 && e.message.includes("ThrottlingException"));
  });

  test("a Gateway tool result becomes the aira-ops body, or an OpsError the model sees", () => {
    assert.deepEqual(m.gw.parseResult("get_ticket", { content: [{ type: "text", text: '{"id":"T-1001"}' }] }), { id: "T-1001" });
    assert.throws(() => m.gw.parseResult("get_ticket", { isError: true, content: [{ type: "text", text: '{"error":{"code":"not_found","message":"no ticket T-9"}}' }] }),
      (e) => e.status === 404 && e.code === "not_found");
    assert.throws(() => m.gw.parseResult("list_jobs", { content: [{ type: "text", text: "<html>" }] }), /returned non-JSON/);
  });

  test("an invocation binds the tenant, runs the same responder and returns the trace", async () => {
    const cfg = m.inv.settings({ GATEWAY_URL: "https://gw.example/mcp", OAUTH_PROVIDER: "p", OAUTH_SCOPES: "aira-ops/read", MODEL_ID: "m",
      GUARDRAIL_ID: "g", GUARDRAIL_VERSION: "1" });
    assert.equal(cfg.maxBudgetUsd, 0.4);
    const reads = { account: async () => ({ contract_sla_minutes: 240, name: "Lab", tier: "gold" }), tickets: async () => ({ tickets: [] }),
      jobs: async () => ({ jobs: [] }), ticket: async () => { throw new Error("unused"); }, config: async () => ({}), close: async () => { reads.closed = true; } };
    const proposal = { summary: "Nothing is at risk or breached for this account at this time.", exposed: [], likely_cause: "",
      evidence: ["sla_report: no open items"], untrusted_instructions_seen: [], action: { type: "none", reason: "nothing exposed" } };
    const replies = [{ stop_reason: "tool_use", content: [{ type: "tool_use", id: "a", name: "sla_report", input: {} }], usage: {} },
      { stop_reason: "tool_use", content: [{ type: "tool_use", id: "b", name: "submit_proposal", input: proposal }], usage: {} }];
    const svc = new m.inv.InvocationService(cfg, { gatewayToken: async (wat) => { assert.equal(wat, "wat-1"); return "tok"; } },
      async (url, tok) => { assert.equal(tok, "tok"); return reads; }, () => ({ messages: async () => replies.shift() }));
    await assert.rejects(svc.invoke({ prompt: "x" }, "s", "wat-1"), /account_id and as_of are required/);
    const out = await svc.invoke({ prompt: "Anything due?", account_id: "ACC-1005", as_of: "2026-09-24T10:30:00+05:30" }, "s1", "wat-1");
    assert.equal(out.status, "no_action");
    assert.equal(out.mode, "agentcore");
    assert.equal(out.as_of, "2026-09-24T10:30:00+05:30");
    assert.ok(out.trace.some((s) => s.name === "guardrail.verify"), "the JSONL span records come back with the result");
    assert.ok(reads.closed, "the Gateway session is closed");
  });

  test("the HTTP contract: /ping, /invocations with any content type, 400 for a bad request", async () => {
    const service = { invoke: async (p, session, wat) => {
      if (!p.account_id) throw new m.inv.BadRequest("account_id and as_of are required");
      return { run_id: "r", session, wat };
    } };
    const srv = http.createServer(m.server.handler(() => service));
    await new Promise((r) => srv.listen(0, "127.0.0.1", r));
    const base = `http://127.0.0.1:${srv.address().port}`;
    try {
      assert.equal((await (await fetch(`${base}/ping`)).json()).status, "Healthy");
      const ok = await fetch(`${base}/invocations`, { method: "POST", body: '{"account_id":"ACC-1001"}',
        headers: { "content-type": "application/octet-stream", [m.server.SESSION]: "sess-1", [m.server.WAT]: "wat-1" } });
      assert.deepEqual(await ok.json(), { run_id: "r", session: "sess-1", wat: "wat-1" });
      const bad = await fetch(`${base}/invocations`, { method: "POST", body: "{}" });
      assert.equal(bad.status, 400);
      assert.equal((await bad.json()).error, "account_id and as_of are required");
      assert.equal((await fetch(`${base}/invocations`, { method: "POST", body: "[1]" })).status, 400);
    } finally { srv.close(); }
  });

  test("the runtime role is least privilege and the environment is exactly what settings() reads", () => {
    const shared = { gateway_url: "https://gw.example/mcp", guardrail_id: "gr1", guardrail_version: 3, clients: { investigator: { scopes: ["aira-ops/read"] } },
      providers: { investigator: { name: "inv-oauth", arn: "arn:aws:bedrock-agentcore:ap-south-1:111122223333:token-vault/default/oauth2credentialprovider/inv-oauth" } } };
    const f = path.join(TMP, "state.json");
    fs.writeFileSync(f, JSON.stringify(shared));
    const env = new m.aws.AwsEnv({ region: "ap-south-1", account: "111122223333", prefix: "aira-d4", modelId: "m", sharedPath: f, ownPath: path.join(TMP, "own.json") });
    assert.equal(env.runtimeName, "aira_d4cap_node_responder");
    assert.equal(env.roleName, "aira-d4-capstone-node-runtime");
    const vars = m.aws.environment(env);
    assert.equal(Object.keys(m.inv.settings(vars)).length, 9);
    assert.equal(vars.OAUTH_SCOPES, "aira-ops/read");
    const text = JSON.stringify(m.aws.policy(env));
    for (const bad of ["ops-write", "InvokeAgentRuntime", "CreateEvent", "s3:", "iam:", "secretsmanager:*"]) assert.ok(!text.includes(bad), bad);
    assert.ok(!/"Action":"\*"/.test(text), "no wildcard action");
  });

  test("the zip writer produces an archive unzip accepts", { skip: !hasUnzip() && "no unzip on PATH" }, () => {
    const f = path.join(TMP, "t.zip");
    m.zip.zip([{ name: "app.js", data: Buffer.from("console.log(1)\n") }, { name: "a/b.mjs", data: Buffer.alloc(5000, 97) }], f);
    const list = execFileSync("unzip", ["-l", f]).toString();
    assert.ok(list.includes("app.js") && list.includes("a/b.mjs"), list);
    execFileSync("unzip", ["-tq", f]);
  });
});

function hasUnzip() { try { execFileSync("unzip", ["-v"], { stdio: "ignore" }); return true; } catch { return false; } }

after(() => fs.rmSync(TMP, { recursive: true, force: true }));
