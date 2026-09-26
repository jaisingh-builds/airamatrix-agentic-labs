// Persisted state, SQLite through node:sqlite (standard library, Node 22.13+; the Lab 5.1 store's shape).
// Every hand-off is a row: the run, the proposal with its SLA snapshot and guardrail verdict, the HUMAN decision
// (who, why, the hash of what they saw), and the operation id of the one write - stored before it is sent.
// The tables are the same in the Java, Python and Node solutions, so one language can read another's DB.
//
// Run statuses, forward only:
//   failed | guardrail_intervened | no_action | blocked | awaiting_approval -> approved | rejected
//   approved -> applied | apply_failed | outcome_unknown (apply again: same operation id)
import crypto from "node:crypto";
import { SetupError } from "./repo.mjs";
import { cut } from "./sla.mjs";
import { nowIst } from "./time.mjs";

const SCHEMA = [
  "create table if not exists runs(id text primary key, account_id text, as_of text, question text, mode text, "
    + "status text, cost_usd real, turns integer, tool_calls integer, error text, trace text, created_at text, updated_at text)",
  "create table if not exists proposals(run_id text primary key, proposal text, sla text, verdict text, sha text, trajectory text)",
  "create table if not exists approvals(run_id text primary key, decision text, approver text, principal text, "
    + "reason text, at text, proposal_sha text)",
  "create table if not exists operations(run_id text primary key, op_id text, action text, payload text, status text, "
    + "response text, created_at text, updated_at text)",
];

/** Bad input from the command line (a run id that does not exist, a missing flag): "refused: ...", exit 3. */
export class Refused extends Error {}

/** Two writers raced for a row only one may create (two people deciding one run). */
export class Conflict extends Error {}

export const newId = () => crypto.randomUUID().replace(/-/g, "").slice(0, 10);

/** Fingerprint of the exact proposal a human decides on (canonical JSON: keys sorted recursively, no whitespace). */
export function sha(value) {
  const sortDeep = (v) => Array.isArray(v) ? v.map(sortDeep)
    : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortDeep(v[k])])) : v;
  return crypto.createHash("sha256").update(JSON.stringify(sortDeep(value)), "utf8").digest("hex");
}

const str = (v) => (v == null ? null : JSON.stringify(v));
const json = (s) => (s == null ? null : JSON.parse(s));

const runRow = (r) => ({ id: r.id, accountId: r.account_id, asOf: r.as_of, question: r.question, mode: r.mode, status: r.status,
  costUsd: Number(r.cost_usd || 0), turns: Number(r.turns || 0), toolCalls: Number(r.tool_calls || 0), error: r.error,
  trace: r.trace, createdAt: r.created_at });

export class Store {
  static async open(file) {
    let DatabaseSync;
    try { ({ DatabaseSync } = await import("node:sqlite")); } catch {
      throw new SetupError(`node:sqlite is not available in Node ${process.versions.node} - use Node 22.13 or newer`);
    }
    try {
      const db = new DatabaseSync(file);
      for (const sql of SCHEMA) db.exec(sql);
      return new Store(db);
    } catch (e) {
      throw new SetupError(`cannot open ${file}: ${e.message}`);
    }
  }

  constructor(db) { this.db = db; }

  exec(sql, ...args) { this.db.prepare(sql).run(...args); }

  createRun(id, accountId, asOf, question, mode) {
    this.exec("insert into runs(id, account_id, as_of, question, mode, status, cost_usd, turns, tool_calls, created_at, updated_at) "
      + "values(?,?,?,?,?,?,0,0,0,?,?)", id, accountId, asOf, question ?? null, mode, "created", nowIst(), nowIst());
    return id;
  }

  finishRun(id, status, cost, turns, toolCalls, error, trace) {
    this.exec("update runs set status=?, cost_usd=?, turns=?, tool_calls=?, error=?, trace=?, updated_at=? where id=?",
      status, cost, turns, toolCalls, error ?? null, trace ?? null, nowIst(), id);
  }

  setStatus(id, status) { this.exec("update runs set status=?, updated_at=? where id=?", status, nowIst(), id); }

  run(id) {
    const r = this.db.prepare("select * from runs where id=?").get(id);
    if (!r) throw new Refused(`no run ${id}`);
    return runRow(r);
  }

  runs(limit) { return this.db.prepare("select * from runs order by created_at desc, rowid desc limit ?").all(limit).map(runRow); }

  saveProposal(id, proposal, sla, verdict, trajectory) {
    this.exec("insert or replace into proposals values(?,?,?,?,?,?)", id, str(proposal), str(sla), str(verdict), sha(proposal), str(trajectory));
  }

  proposal(id) {
    const r = this.db.prepare("select * from proposals where run_id=?").get(id);
    return r ? { proposal: json(r.proposal), sla: json(r.sla), verdict: json(r.verdict), sha: r.sha, trajectory: json(r.trajectory) } : null;
  }

  /** The decision is bound to the proposal as the human saw it. The primary key is the real "one decision" guarantee. */
  recordDecision(id, decision, approver, principal, reason, proposalSha) {
    try {
      this.exec("insert into approvals values(?,?,?,?,?,?,?)", id, decision, approver, principal, reason, nowIst(), proposalSha);
    } catch (e) {
      if (String(e.message).includes("UNIQUE")) throw new Conflict(`run ${id} was already decided`);
      throw e;
    }
  }

  approval(id) {
    const r = this.db.prepare("select * from approvals where run_id=?").get(id);
    return r ? { decision: r.decision, approver: r.approver, principal: r.principal, reason: r.reason, at: r.at, proposalSha: r.proposal_sha } : null;
  }

  recordOperation(id, opId, action, payload) {
    this.exec("insert into operations values(?,?,?,?,?,?,?,?)", id, opId, action, str(payload), "pending", null, nowIst(), nowIst());
  }

  operationResult(id, status, response) {
    this.exec("update operations set status=?, response=?, updated_at=? where run_id=?", status, cut(response, 4000), nowIst(), id);
  }

  operation(id) {
    const r = this.db.prepare("select * from operations where run_id=?").get(id);
    return r ? { opId: r.op_id, action: r.action, payload: json(r.payload), status: r.status, response: r.response } : null;
  }

  close() { try { this.db.close(); } catch { /* closing */ } }
}
