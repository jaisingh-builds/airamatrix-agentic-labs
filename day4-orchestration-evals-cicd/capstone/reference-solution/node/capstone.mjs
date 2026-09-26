#!/usr/bin/env node
// Local mode - your own aira-ops, the training gateway, no AWS. Node 22.13+, standard library + labkit only.
//
//   node capstone.mjs tokens --account ACC-1001          read token (agent) + apply token (human), shown once
//   node capstone.mjs run --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
//   node capstone.mjs show RUN | list | trace RUN
//   node capstone.mjs approve RUN --by "Name" --reason "why"      (or reject)
//   node capstone.mjs apply RUN                                   (its own shell: AIRA_OPS_APPLY_TOKEN)
//   node capstone.mjs replay ../fixtures/blocked-leak.json        (a saved proposal through the guardrail, $0)
//   node capstone.mjs eval [--repeat 2] [--cases a,b] [--budget 1.5] | eval --regrade results/x.json
//
// Environment: AIRA_OPS_URL (default http://127.0.0.1:8150), AIRA_OPS_READ_TOKEN (run/replay), AIRA_OPS_APPLY_TOKEN
// (apply only), CAPSTONE_DB (default ./capstone-runs.sqlite), LAB_TRACE_DIR (default <repo>/traces), and .env for the gateway.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Config, GatewayClient } from "../../../../labkit/node/agentic-core.mjs";
import { FORBIDDEN_ENV, ResponderAgent, localModel } from "./lib/agent.mjs";
import * as evals from "./lib/evals.mjs";
import { GateError, HttpOpsWriter, apply, decide } from "./lib/gate.mjs";
import { verify } from "./lib/guardrails.mjs";
import { HttpOpsReader } from "./lib/ops.mjs";
import { PrivateOps, hex, issue } from "./lib/private-ops.mjs";
import { NODE_DIR, SetupError, opsScript, solution } from "./lib/repo.mjs";
import * as responder from "./lib/responder.mjs";
import { compute } from "./lib/sla.mjs";
import { Tracer, redact, renderTrace } from "./lib/spans.mjs";
import { Refused, Store, newId } from "./lib/store.mjs";
import { Verdict } from "./lib/guardrails.mjs";
import { Instant, InvalidInstant, parseInstant } from "./lib/time.mjs";

export const USAGE = `node capstone.mjs <command>      (local mode: your aira-ops + the training gateway)
  tokens  --account ACC-1001 [--callers FILE]
  run     --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question TEXT] [--budget USD] [--max-turns N]
  show RUN | list | trace RUN
  approve RUN --by NAME --reason WHY        reject RUN --by NAME --reason WHY
  apply   RUN                                (needs AIRA_OPS_APPLY_TOKEN; starts no agent)
  replay  FIXTURE.json                       (a saved proposal through the guardrail; no model)
  eval    [--repeat N] [--cases a,b] [--budget USD] [--golden F] | eval --regrade RESULTS.json`;

/** --key value options and positional arguments. */
export class Opts {
  constructor(argv) {
    this.kv = {}; this.positional = [];
    for (let i = 0; i < argv.length; i++) {
      if (argv[i].startsWith("--")) {
        if (i + 1 >= argv.length) throw new Refused(`${argv[i]} needs a value`);
        this.kv[argv[i].slice(2)] = argv[++i];
      } else this.positional.push(argv[i]);
    }
  }
  has(k) { return k in this.kv; }
  get(k) { return this.kv[k]; }
  getOr(k, d) { return this.has(k) ? this.kv[k] : d; }
  need(k) { if (!this.has(k)) throw new Refused(`--${k} is required`); return this.kv[k]; }
  num(k, d, int) {
    if (!this.has(k)) return d;
    const v = Number(this.kv[k]);
    if (!Number.isFinite(v) || (int && !Number.isInteger(v))) throw new Refused(`For input string: "${this.kv[k]}"`);
    return v;
  }
  intOr(k, d) { return this.num(k, d, true); }
  dblOr(k, d) { return this.num(k, d, false); }
  pos(i) { if (this.positional.length <= i) throw new Refused("missing RUN id / file"); return this.positional[i]; }
}

function need(env, k, what) {
  const v = env[k];
  if (!v || !v.trim()) throw new SetupError(`${k} is not set - ${what}`);
  return v;
}

const f4 = (n) => Number(n || 0).toFixed(4);
const traceFor = (r) => new Tracer("capstone", r.id);   // same file as the run, as long as LAB_TRACE_DIR is the same

/** A saved proposal through the SAME guardrail and gate - every learner gets the refusal on demand. */
export async function replay(store, fx, ops) {
  const acc = fx.account ?? "";
  const asOf = parseInstant(fx.as_of);
  const rid = store.createRun(newId(), acc, asOf.toString(), `${fx.question ?? ""} [replay of ${fx.source ?? ""}]`, "local");
  const tr = new Tracer("capstone", rid);
  let sla, v;
  await tr.within("replay", { account: acc, stage: "sla-responder" }, async (root) => {
    await tr.within("guardrail.verify", { stage: "code-guardrail" }, async (vs) => {
      sla = await compute(ops, acc, asOf);
      v = verify(fx.proposal, sla);
      vs.set("verdict", v.passed ? "pass" : "block").set("denials", v.rules());
      if (!v.passed) vs.fail(`guardrail refused: ${v.rules().join(", ")}`);
    });
    root.set("verdict", v.passed ? "pass" : "blocked");
  });
  const action = fx.proposal?.action?.type;
  const status = !v.passed ? "blocked" : action === "none" ? "no_action" : "awaiting_approval";
  store.finishRun(rid, status, 0, 0, 0, null, tr.path);
  store.saveProposal(rid, fx.proposal, sla.toJson(), v.toJson(), []);
  return rid;
}

export function show(store, rid, out) {
  const r = store.run(rid);
  out(`run ${r.id} · ${r.mode} · ${r.accountId} · as_of ${r.asOf} · ${r.status} · $${f4(r.costUsd)} · ${r.turns} turns · ${r.toolCalls} tool calls`);
  if (r.question != null) out(`  request: ${r.question}`);
  if (r.error != null) out(`  error:   ${r.error}`);
  const p = store.proposal(rid);
  if (p) {
    if (p.sla) {
      out("\n[sla] computed by code at as_of:");
      for (const i of p.sla.items || []) {
        out(`  ${String(i.item).padEnd(7)} ${String(i.kind).padEnd(7)} ${String(i.status).padEnd(12)} ${String(i.elapsed_minutes).padStart(5)} / `
          + `${String(i.target_minutes).padEnd(5)} min  ${String(i.pct_of_target).padStart(3)}%  ${i.state}`);
      }
    }
    const pr = p.proposal || {};
    out("\n[proposal]");
    out(`  summary: ${pr.summary ?? ""}`);
    out(`  cause:   ${pr.likely_cause ?? ""}`);
    const ex = (pr.exposed || []).map((e) => `${e.item} ${e.state}`);
    out(`  exposed: ${ex.length ? ex.join(", ") : "(none)"}`);
    if ((pr.untrusted_instructions_seen || []).length) out(`  flagged: ${JSON.stringify(pr.untrusted_instructions_seen)} (instructions in ticket text - not followed)`);
    const a = pr.action || {};
    out(`  action:  ${a.type ?? ""}${"ticket_id" in a ? ` on ${a.ticket_id}` : ""} - ${a.reason ?? ""}`);
    if ("comment" in a) out(`  comment (customer-visible):\n    ${String(a.comment).replace(/\n/g, "\n    ")}`);
    if (p.verdict) {
      const v = Verdict.fromJson(p.verdict);
      out(`\n[guardrail] ${v.passed ? "PASS - every rule" : "BLOCKED"}`);
      for (const d of v.denials) out(`  x ${d.rule}: ${d.detail}`);
    }
  }
  const ap = store.approval(rid);
  if (ap) out(`\n[gate] ${ap.decision} by ${ap.approver} (${ap.principal}) at ${ap.at}: ${ap.reason}`);
  const op = store.operation(rid);
  if (op) out(`\n[apply] ${op.status} · op ${op.opId} · ${op.response}`);
  if (r.status === "awaiting_approval") out(`\nnext: approve ${rid} --by "Your Name" --reason "why"   (or reject)`);
  if (r.status === "approved" && r.mode === "local") out(`\nnext: apply ${rid}   (in the shell that holds AIRA_OPS_APPLY_TOKEN)`);
}

function tokens(o, out) {
  const acc = o.need("account");
  const callers = path.resolve(o.getOr("callers", "capstone-callers.json"));
  const admin = `unused-${hex(4)}`;       // --issue-token only edits the callers file
  const read = issue(callers, admin, "sla-responder", acc, false);
  const write = issue(callers, admin, "capstone-apply", acc, true);
  out(`# Shown once; ${path.basename(callers)} keeps only their SHA-256. Both are scoped to ${acc}.`);
  out(`export AIRA_OPS_READ_TOKEN=${read}     # shell 1: the agent (read-only)`);
  out(`export AIRA_OPS_APPLY_TOKEN=${write}    # shell 2: apply, the human's step - never in shell 1`);
  out(`# PowerShell: $env:AIRA_OPS_READ_TOKEN="${read}"  /  $env:AIRA_OPS_APPLY_TOKEN="${write}"`);
  out("# start YOUR aira-ops with this callers file and your own db and port, e.g.:");
  out(`#   python3 "${opsScript()}" --port 8177 --db capstone-ops.sqlite --callers "${callers}" --reset`);
  return 0;
}

async function evalCmd(o, env, out) {
  const golden = evals.load(o.getOr("golden", path.join(solution(), "golden", "cases.json")));
  if (o.has("regrade")) return evals.regrade(golden, o.get("regrade"), out);
  const cases = evals.select(golden, o.get("cases"));
  if (!cases.length) { out("no matching cases"); return 2; }
  let cfg;
  try { cfg = new Config().require(); } catch (e) { out(`setup: ${e.message}`); return 2; }
  for (const k of FORBIDDEN_ENV) if (env[k]) { out(`setup: unset ${k} - evals start agents`); return 2; }
  const model = localModel(new GatewayClient(cfg));
  const maxTurns = o.intOr("max-turns", 10);
  const ops = await PrivateOps.start(evals.accounts(cases), false);
  try {
    return await evals.execute(golden, cases, {
      repeat: o.intOr("repeat", 1), workers: o.intOr("workers", 3), budget: o.dblOr("budget", 1.5), target: "local",
      runner: evals.localRunner(ops, (budget) => new ResponderAgent(model, cfg.model, maxTurns, budget), o.dblOr("per-run-budget", 0.30)),
      outDir: o.getOr("out", path.join(NODE_DIR, "results")), out,
    });
  } finally {
    await ops.close();
  }
}

export async function main(argv, env = process.env, out = (l) => console.log(l), err = (l) => console.error(l)) {
  if (!argv.length || argv[0].startsWith("-h")) { out(USAGE); return argv.length ? 0 : 2; }
  const url = env.AIRA_OPS_URL || "http://127.0.0.1:8150";
  let store;
  try {
    const o = new Opts(argv.slice(1));
    if (argv[0] === "tokens") return tokens(o, out);
    if (argv[0] === "eval") return await evalCmd(o, env, out);
    const known = ["run", "replay", "show", "list", "trace", "approve", "reject", "apply"];
    if (!known.includes(argv[0])) { err(`unknown command ${argv[0]}\n${USAGE}`); return 2; }
    store = await Store.open(env.CAPSTONE_DB || "capstone-runs.sqlite");
    switch (argv[0]) {
      case "run": {
        const read = need(env, "AIRA_OPS_READ_TOKEN", "the agent's read-only token (capstone tokens)");
        const acc = o.need("account");
        const asOf = o.has("as-of") ? parseInstant(o.get("as-of")) : Instant.now();
        const rid = store.createRun(newId(), acc, asOf.toString(), o.get("question"), "local");
        const tr = new Tracer("capstone", rid);
        out(`run ${rid} started · trace ${tr.path}`);
        const cfg = new Config();
        let gw;
        try { gw = new GatewayClient(cfg); } catch (e) { throw new SetupError(e.message); }
        const agent = new ResponderAgent(localModel(gw), cfg.model, o.intOr("max-turns", Math.max(cfg.maxSteps, 10)),
          o.dblOr("budget", cfg.budgetUsd), (k) => env[k]);
        const res = await responder.run(rid, acc, asOf, o.get("question"), new HttpOpsReader(url, read), agent, tr);
        responder.save(store, res, tr.path);
        show(store, rid, out);
        return res.status === "blocked" ? 3 : res.status === "failed" || res.status === "guardrail_intervened" ? 1 : 0;
      }
      case "replay": {
        const read = need(env, "AIRA_OPS_READ_TOKEN", "a read-only token: the guardrail verifies against live data");
        const file = o.pos(0);
        const fx = JSON.parse(fs.readFileSync(file, "utf8"));
        const rid = await replay(store, fx, new HttpOpsReader(url, read));
        out(`run ${rid} replayed from ${file} (no model, $0)`);
        show(store, rid, out);
        return store.run(rid).status === "blocked" ? 3 : 0;
      }
      case "show": show(store, o.pos(0), out); return 0;
      case "list":
        for (const r of store.runs(20)) {
          out(`${r.id}  ${r.mode.padEnd(6)} ${String(r.accountId).padEnd(9)} ${r.status.padEnd(19)} $${f4(r.costUsd).padEnd(7)} ${r.asOf}`);
        }
        return 0;
      case "trace": renderTrace(store.run(o.pos(0)).trace, out); return 0;
      case "approve": case "reject": {
        const r = store.run(o.pos(0));
        decide(store, r.id, argv[0], o.get("by"), `os:${os.userInfo().username}`, o.get("reason"), traceFor(r));
        show(store, r.id, out);
        return 0;
      }
      case "apply": {
        const write = need(env, "AIRA_OPS_APPLY_TOKEN", "the apply step's own write token (capstone tokens)");
        const r = store.run(o.pos(0));
        await apply(store, r.id, new HttpOpsWriter(url, write), url, traceFor(r));
        show(store, r.id, out);
        return store.run(r.id).status === "applied" ? 0 : 1;
      }
    }
    return 2;
  } catch (e) {
    if (e instanceof GateError || e instanceof Refused || e instanceof InvalidInstant) { err(`refused: ${e.message}`); return 3; }
    if (e instanceof SetupError) { err(e.message); return 2; }
    err(`${e.constructor?.name || "Error"}: ${redact(String(e.message), 0)}`);
    return 1;
  } finally {
    store?.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; });
}
