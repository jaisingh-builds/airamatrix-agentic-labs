// THE HUMAN APPROVAL POINT and the one write.
//
// decide(): a named person, a reason, one decision per run, bound to the hash of the proposal they saw.
//           Refused: no name, no reason (or a one-word one), an agent identity as approver, a run that is not
//           waiting, a run the guardrail blocked (no override exists), a second decision.
// apply():  plain code, not an agent, in its own process with the only write credential. Checks the DECISION
//           RECORD (not the status field), the proposal hash, the write host, the outbound guardrail again and the
//           ticket's current state; stores the operation id BEFORE sending so a retry writes at most once.
import crypto from "node:crypto";
import { Verdict, outbound } from "./guardrails.mjs";
import { OPEN_TICKET, Report, cut } from "./sla.mjs";
import { redact } from "./spans.mjs";
import { Conflict, Refused, sha } from "./store.mjs";

export class GateError extends Error {}

/** Names an agent or service identity uses. An agent cannot approve its own proposal. */
export const AGENT_IDENTITIES = new Set(["sla-responder", "capstone-agent", "investigator", "reviewer", "supervisor",
  "pipeline-agents", "pipeline-apply"]);
const AGENTISH = /(^|[^a-z])(agent|bot|runtime|claude|llm)([^a-z]|$)/i;
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** A refusal is part of the story of a run: it goes in the trace too (our message, never the human's reason). */
function refused(tr, what, e) {
  if (e instanceof GateError) tr.event("gate.refused", { decision: what, reason: cut(e.message, 200) });
  throw e;
}

export function decide(store, rid, decision, approver, principal, reason, tr) {
  try { return decideOrRefuse(store, rid, decision, approver, principal, reason, tr); } catch (e) { return refused(tr, decision, e); }
}

export async function apply(store, rid, writer, opsUrl, tr) {
  try { return await applyOrRefuse(store, rid, writer, opsUrl, tr); } catch (e) { return refused(tr, "apply", e); }
}

function decideOrRefuse(store, rid, decision, approver, principal, reason, tr) {
  if (decision !== "approve" && decision !== "reject") throw new Refused("decision must be approve or reject");
  const who = (approver ?? "").trim(), why = (reason ?? "").trim();
  if (!who || !why) throw new GateError("a decision needs --by (who) and --reason (why)");
  if (why.length < 10 || !why.includes(" ")) throw new GateError(`--reason must say why in a sentence, not '${why}'`);
  if (AGENT_IDENTITIES.has(who.toLowerCase()) || AGENTISH.test(who)) {
    throw new GateError(`'${who}' is an agent or service identity - a person decides, not the agent that proposed it`);
  }
  const r = store.run(rid);
  if (store.approval(rid)) throw new GateError(`run ${rid} was already decided`);
  if (r.status === "blocked") {
    const rules = Verdict.fromJson(store.proposal(rid)?.verdict).rules();
    throw new GateError(`the guardrail blocked this proposal (${rules.join(", ")}). There is no override: fix the cause and run again.`);
  }
  if (r.status !== "awaiting_approval") throw new GateError(`run ${rid} is ${r.status}, not waiting for a decision`);
  const p = store.proposal(rid);
  try {
    store.recordDecision(rid, decision, who, principal, why, p.sha);
  } catch (e) {
    if (e instanceof Conflict) throw new GateError(e.message);
    throw e;
  }
  store.setStatus(rid, decision === "approve" ? "approved" : "rejected");
  tr.event("gate.decided", { decision, approver: who });     // the reason stays in the store, not the trace
  return store.run(rid);
}

/** aira-ops with the apply token: one read (current state) and one idempotent comment. Tests pass a fake. */
export class HttpOpsWriter {
  constructor(base, token, timeoutMs = 8000) { this.base = base.replace(/\/+$/, ""); this.token = token; this.timeoutMs = timeoutMs; }
  getTicket(id) { return this.send("GET", `/tickets/${encodeURIComponent(id)}`); }
  postComment(id, comment, key) {
    return this.send("POST", `/tickets/${encodeURIComponent(id)}/comments`, JSON.stringify({ body: comment }),
      { "content-type": "application/json", "idempotency-key": key });
  }
  async send(method, p, body, headers = {}) {
    try {
      const r = await fetch(this.base + p, { method, body, headers: { ...headers, authorization: `Bearer ${this.token}` },
        signal: AbortSignal.timeout(this.timeoutMs) });
      const text = await r.text();
      let b = {};
      try { b = text ? JSON.parse(text) : {}; } catch { b = { error: "invalid JSON" }; }
      return { status: r.status, body: b };
    } catch (e) {
      return { status: 0, body: { error: redact(e.name === "TimeoutError" ? "HttpTimeoutException" : "ConnectException", 0) } };
    }
  }
}

async function applyOrRefuse(store, rid, writer, opsUrl, tr) {
  const r = store.run(rid);
  const a = store.approval(rid);
  if (!a || a.decision !== "approve") throw new GateError(`run ${rid} has no approval on record`);
  const p = store.proposal(rid);
  if (!p || sha(p.proposal) !== a.proposalSha) {
    throw new GateError(`run ${rid}: the proposal changed after it was decided - it needs a new decision`);
  }
  if (r.status === "applied") return r;                                   // applying again is a no-op, not an error
  if (r.status !== "approved" && r.status !== "outcome_unknown") {
    throw new GateError(`run ${rid} is ${r.status}; only an approved run can be applied`);
  }
  if (r.mode !== "local") {
    throw new GateError(`run ${rid} read the SHARED aira-ops through the AgentCore Gateway. The decision is recorded; `
      + "the write is made in local mode only (classroom rule: nobody writes to the shared aira-ops)");
  }
  const host = new URL(opsUrl).hostname;
  const allowed = process.env.CAPSTONE_ALLOW_WRITE_HOST;
  if (!LOCAL_HOSTS.has(host) && host !== allowed) {
    throw new GateError(`refusing to write to ${host}: apply writes only to your own aira-ops on this machine `
      + "(set CAPSTONE_ALLOW_WRITE_HOST to that host name to allow another)");
  }
  const action = p.proposal.action || {};
  if (action.type !== "post_customer_update") throw new GateError(`run ${rid} has nothing to apply`);
  const tid = action.ticket_id, comment = action.comment;
  const again = outbound(comment, Report.fromJson(p.sla || {}));           // defence in depth, at the write
  if (again.length) throw new GateError(`outbound guardrail refused the comment: ${again[0].rule}`);

  let op = store.operation(rid);
  if (!op) {                                                               // stored BEFORE the request is sent
    store.recordOperation(rid, crypto.randomUUID(), "post_customer_update", { ticket_id: tid, comment });
    op = store.operation(rid);
  }
  if (op.status === "done") { store.setStatus(rid, "applied"); return store.run(rid); }

  await tr.within("apply", { action: "post_customer_update", op_id: op.opId, approver: a.approver, input: { ticket_id: tid } }, async (sp) => {
    const now = await writer.getTicket(tid);                               // the world may have moved while the human decided
    if (now.status === 404) { sp.fail("not visible to the write credential"); throw new GateError(`${tid} is not visible to the apply credential`); }
    const st = now.body?.status;
    if (now.status === 200 && !OPEN_TICKET.has(st)) {
      sp.fail(`stale: ticket is ${st}`);
      throw new GateError(`${tid} is ${st} now - the update is stale; nothing was written`);
    }
    const resp = await writer.postComment(tid, comment, op.opId);
    sp.set("http_status", resp.status).set("replayed", resp.body?._replayed === true);
    if (resp.status === 200 || resp.status === 201) {
      store.operationResult(rid, "done", `HTTP ${resp.status}`);
      store.setStatus(rid, "applied");
    } else if (resp.status === 0 || resp.status >= 500) {
      store.operationResult(rid, "pending", JSON.stringify(resp.body));
      store.setStatus(rid, "outcome_unknown");
      sp.fail("outcome unknown - run apply again; the same operation id makes it safe");
    } else {
      store.operationResult(rid, "failed", JSON.stringify(resp.body));
      store.setStatus(rid, "apply_failed");
      sp.fail(`HTTP ${resp.status}`);
    }
  });
  return store.run(rid);
}
