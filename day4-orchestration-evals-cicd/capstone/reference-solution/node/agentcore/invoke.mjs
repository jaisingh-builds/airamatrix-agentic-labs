// InvokeAgentRuntime for the capstone runtime, then the SAME local bookkeeping as a local run: the run and its
// proposal go into the capstone store (mode agentcore) and the returned span records into
// <LAB_TRACE_DIR>/capstone-<run>.jsonl - so show / trace / approve work unchanged. A long read timeout and NO automatic
// retry: an SDK retry after a timeout would start a second, parallel run.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { NodeHttpHandler } from "@smithy/node-http-handler";
import { SetupError } from "../lib/repo.mjs";
import { Tracer, redact } from "../lib/spans.mjs";
import { newId } from "../lib/store.mjs";

export async function call(env, account, asOf, prompt, actor, runId) {
  const arn = env.own().runtime?.arn;
  if (!arn) throw new SetupError(`no runtime in ${path.basename(env.ownPath)} - run deploy first`);
  const payload = { prompt: prompt ?? "", actor_id: actor, account_id: account, as_of: asOf, run_id: runId };
  const rt = new BedrockAgentCoreClient({ region: env.region, maxAttempts: 1,
    requestHandler: new NodeHttpHandler({ requestTimeout: 600000, connectionTimeout: 10000 }) });
  const session = `capstone-${crypto.randomUUID().replace(/-/g, "")}`;
  const r = await rt.send(new InvokeAgentRuntimeCommand({ agentRuntimeArn: arn, runtimeSessionId: session,
    runtimeUserId: actor, contentType: "application/json", payload: Buffer.from(JSON.stringify(payload)) }));
  const body = Buffer.from(await r.response.transformToByteArray()).toString("utf8");
  let out;
  try { out = JSON.parse(body); } catch { throw new SetupError(`the runtime answered non-JSON: ${redact(body.slice(0, 300), 0)}`); }
  remember(env, { session, run_id: out.run_id ?? null, status: out.status ?? null });
  return out;
}

/** The last runtime sessions (gitignored own state), so `score` can hand them to AgentCore Evaluations. */
function remember(env, s) {
  try { env.saveOwn("sessions", [...(env.own().sessions || []), s].slice(-20)); } catch { /* bookkeeping only */ }
}

/** Store an AgentCore run like a local one and write its trace file. Returns the trace path. */
export function record(store, r, question) {
  const rid = r.run_id;
  if (!rid) throw new SetupError(`the runtime returned no run: ${redact(JSON.stringify(r), 0)}`);
  const tr = new Tracer("capstone", rid);
  fs.appendFileSync(tr.path, (r.trace || []).map((s) => JSON.stringify(s) + "\n").join(""));
  store.createRun(rid, r.account_id ?? "", r.as_of ?? "", question ?? null, "agentcore");
  store.finishRun(rid, r.status ?? "failed", r.cost_usd ?? 0, r.turns ?? 0, r.tool_calls ?? 0, r.error ?? null, tr.path);
  if (r.proposal) store.saveProposal(rid, r.proposal, r.sla ?? null, r.verdict ?? null, r.trajectory ?? []);
  return tr.path;
}

/** The agentcore eval target: one invocation per case, graded exactly like local runs. */
export function evalTarget(env) {
  return async (kase) => {
    const t0 = Date.now();
    try {
      const r = await call(env, kase.account, kase.as_of, kase.question ?? "", "capstone-eval", newId());
      const out = { ...r };
      delete out.trace;
      delete out.sla;
      out.seconds = Math.round((Date.now() - t0) / 100) / 10;
      if (r.status === "failed" || r.status === "guardrail_intervened" || (r.error && !r.proposal)) {
        return { error: r.error ?? r.status, cost_usd: r.cost_usd ?? 0 };
      }
      return out;
    } catch (e) {
      return { error: `${e.name || "Error"}: ${redact(String(e.message), 0)}`, cost_usd: 0 };
    }
  };
}
