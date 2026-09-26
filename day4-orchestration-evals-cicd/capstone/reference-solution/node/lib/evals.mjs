// The eval harness: every golden case x repeats, graded by checks.mjs, gated, written to a results file and a
// Markdown report. A RUN is one attempt plus at most one retry after an ERROR (no proposal) - never after a FAIL.
// The first-attempt pass count is reported beside the final one, so a retry cannot hide flakiness.
//
// Targets: local (a private aira-ops per eval with fresh seed data + the training gateway) or the deployed
// AgentCore runtime (agentcore/cli.mjs eval). Grading is identical. Exit codes: 0 gate passed, 1 gate failed, 2 could not run.
import fs from "node:fs";
import path from "node:path";
import { KNOWN, gate, gradeCase } from "./checks.mjs";
import { HttpOpsReader } from "./ops.mjs";
import { hex } from "./private-ops.mjs";
import * as responder from "./responder.mjs";
import { cut } from "./sla.mjs";
import { Tracer, redact } from "./spans.mjs";
import { parseInstant } from "./time.mjs";

export function load(goldenPath) {
  const g = JSON.parse(fs.readFileSync(goldenPath, "utf8"));
  for (const c of g.cases || []) {
    for (const chk of c.checks || []) if (!KNOWN.has(chk.check)) throw new Error(`${c.id}: unknown check ${chk.check}`);
  }
  return g;
}

export function select(golden, ids) {
  const want = ids ? ids.split(",") : null;
  return (golden.cases || []).filter((c) => !want || !ids.trim() || want.includes(c.id));
}

export const accounts = (cases) => [...new Set(cases.map((c) => c.account))].sort();

const pad = (s, n) => String(s).padEnd(n);
const stamp = () => {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
};

/** Runs, grades, gates, writes results + report. runner(kase) -> result record or {error, cost_usd, trace}; never throws. */
export async function execute(golden, cases, { repeat = 1, workers = 3, budget = 1.5, target, runner, outDir, out = console.log }) {
  const min = golden.gate?.min_pass_rate ?? 1.0;
  const byId = new Map(cases.map((c) => [c.id, { id: c.id, has_critical: (c.checks || []).some((k) => k.critical === true), runs: [] }]));
  let spent = 0;
  const jobs = [];
  for (let rep = 0; rep < repeat; rep++) for (const c of cases) jobs.push(c);
  const worker = async () => {
    for (let c = jobs.shift(); c; c = jobs.shift()) {
      if (spent > budget) continue;
      let r = await runner(c);
      if ("error" in r) {                                          // an ERROR is retried once; a FAIL never is
        spent += r.cost_usd || 0;
        out(`  RETRY ${pad(c.id, 26)} after: ${cut(r.error, 70)}`);
        const again = await runner(c);
        again.retried_after = cut(r.error, 200);
        again.errored_cost_usd = r.cost_usd || 0;
        r = again;
      }
      if (!("error" in r)) r.grade = gradeCase(c, r);
      spent += r.cost_usd || 0;
      byId.get(c.id).runs.push(r);
      const st = "error" in r ? "ERROR" : r.grade.passed ? "PASS" : "FAIL";
      out(`  ${pad(st, 5)} ${pad(c.id, 26)} $${(r.cost_usd || 0).toFixed(3)}  ${r.status ?? ""}`);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, workers) }, worker));
  const results = [...byId.values()];
  const g = gate(results, min);
  if (spent > budget) g.budget_exceeded = true;
  const doc = { suite: golden.suite, target, cost_usd: Math.round(spent * 10000) / 10000, gate: g, cases: results };
  fs.mkdirSync(outDir, { recursive: true });
  const base = path.join(outDir, `eval-${target}-${stamp()}`);
  fs.writeFileSync(`${base}.json`, JSON.stringify(redact(doc, 0), null, 2));     // secrets masked, not truncated (--regrade needs the text)
  const rep = report(doc);
  fs.writeFileSync(`${base}.md`, rep);
  out("");
  out(rep);
  out(`results: ${base}.json`);
  if (g.runs === g.unrecovered_errors) return 2;
  return g.ok ? 0 : 1;
}

/** Re-grade a saved results file with the current golden checks: no model, no cost. */
export function regrade(golden, saved, out = console.log) {
  const prev = JSON.parse(fs.readFileSync(saved, "utf8"));
  const byId = new Map((golden.cases || []).map((c) => [c.id, c]));
  const results = [];
  for (const c of prev.cases || []) {
    const kase = byId.get(c.id);
    if (!kase) continue;
    results.push({ ...c, runs: (c.runs || []).map((r) => ("error" in r ? { ...r } : { ...r, grade: gradeCase(kase, r) })) });
  }
  const g = gate(results, golden.gate?.min_pass_rate ?? 1.0);
  out(report({ suite: `${golden.suite} (re-graded)`, target: prev.target, cost_usd: 0, gate: g, cases: results }));
  return g.ok ? 0 : 1;
}

export function report(doc) {
  const g = doc.gate;
  const lines = [];
  lines.push(`## Eval gate: ${g.ok ? "PASS" : "FAIL"} - ${doc.suite} (target: ${doc.target})`, "");
  lines.push(`${g.passed}/${g.runs} runs passed (${Math.round(100 * g.pass_rate)}%, need ${Math.round(100 * g.min_pass_rate)}%) · `
    + `first attempt ${g.first_attempt_passed}/${g.runs} · ${g.retried} retried after an error · ${g.unrecovered_errors} unrecovered errors · `
    + `$${Number(doc.cost_usd).toFixed(2)}`, "");
  lines.push("| case | runs passed | failing checks |", "|---|---|---|");
  for (const c of doc.cases) {
    let ok = 0;
    const n = c.runs.length;
    const fails = new Set();
    for (const r of c.runs) {
      if ("error" in r) { fails.add(`error: ${cut(r.error, 80)}`); continue; }
      if (r.grade?.passed) ok++;
      for (const ch of r.grade?.checks || []) if (!ch.passed) fails.add(`${ch.critical ? `**${ch.check}**` : ch.check}: ${ch.detail}`);
    }
    const f = [...fails].sort();
    lines.push(`| ${c.id} | ${ok}/${n}${ok > 0 && ok < n ? " (flaky)" : ""} | ${f.length ? f.join("<br>") : "—"} |`);
  }
  if (g.critical_failures.length) {
    lines.push("", "**Critical checks failed** - a safety property is never averaged away:");
    for (const f of g.critical_failures) lines.push(`- ${f}`);
  }
  return lines.join("\n") + "\n";
}

/** The local target: one private aira-ops, a read token per account, the training gateway for the model. */
export function localRunner(ops, makeAgent, perRunBudget) {
  return async (kase) => {
    const rid = `eval-${kase.id}-${hex(3)}`;
    const tr = new Tracer("capstone", rid);
    const t0 = process.hrtime.bigint();
    try {
      const acc = kase.account;
      const reader = new HttpOpsReader(ops.url, ops.readTokens[acc]);
      const o = await responder.run(rid, acc, parseInstant(kase.as_of), kase.question ?? "", reader, makeAgent(perRunBudget), tr);
      const r = o.toJson();
      delete r.sla;
      r.seconds = Math.round(Number(process.hrtime.bigint() - t0) / 1e8) / 10;
      r.trace = path.basename(tr.path);
      if (o.status === "failed" || o.status === "guardrail_intervened") return { error: o.error, cost_usd: o.costUsd, trace: path.basename(tr.path) };
      return r;
    } catch (e) {
      return { error: `${e.constructor?.name || "Error"}: ${cut(String(e.message), 300)}`, cost_usd: 0, trace: path.basename(tr.path) };
    }
  };
}
