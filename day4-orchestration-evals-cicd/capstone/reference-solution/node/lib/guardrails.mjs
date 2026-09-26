// THE GUARDRAIL - in code, not in the prompt. A proposal reaches a human only if every rule passes; a blocked
// proposal cannot be approved, even with an override (a guardrail a human can click past is a warning). The same
// outbound rules run again inside gate.apply(), at the point of the write.
//
// rule                    refuses
// contract.*              output that does not match sla-proposal.json, or an action missing its fields
// claims.unknown_item     an "exposed" item sla_report does not have (invented)
// claims.wrong_state      at_risk vs breached wrong
// claims.wrong_numbers    elapsed/target minutes not what the code computed (+-2 min)
// claims.omitted          an at_risk/breached item left out (hiding exposure from the duty manager)
// action.out_of_scope     a ticket that is not this account's (another tenant, or does not exist)
// action.not_exposed      a customer update on a ticket that is not at_risk/breached
// comment.length          under 40 or over 700 characters
// comment.secret          anything secret-shaped: bearer/sk-/hex tokens, a secret env value, AIRA_OPS_* names
// comment.internal_config an internal setting name (ingest.*, alerts.*, viewer.*, feature.*)
// comment.other_tenant    another account's id (ACC-nnnn)
// comment.foreign_id      a ticket or job id that is not this account's
// comment.link            a URL - customer updates carry no links (a classic exfiltration channel)
import { ContractError, SCHEMA, validate } from "./contracts.mjs";
import { cut } from "./sla.mjs";
import { redact } from "./spans.mjs";

export { SCHEMA };
export const TOLERANCE_MIN = 2;
const CONFIG_NAME = /\b(ingest|alerts|viewer|feature)\.[a-z_]+/i;
const ACCOUNT_ID = /\bACC-\d{4}\b/g;
const OBJECT_ID = /\b[TJ]-\d{4}\b/g;
const LINK = /\b(https?:\/\/|www\.)/i;
const SECRET_WORDS = /\b(AIRA_OPS_[A-Z_]+|ANTHROPIC_[A-Z_]+|bearer\s+\S{8,})/i;

export class Verdict {
  constructor(passed, denials) { this.passed = passed; this.denials = denials; }
  rules() { return [...new Set(this.denials.map((d) => d.rule))]; }
  toJson() { return { passed: this.passed, denials: this.denials.map((d) => ({ rule: d.rule, detail: d.detail })) }; }
  static fromJson(n) { return new Verdict(n?.passed === true, (n?.denials || []).map((d) => ({ rule: d.rule ?? "", detail: d.detail ?? "" }))); }
}

const has = (o, k) => o && Object.prototype.hasOwnProperty.call(o, k);

/** Where a missing top-level key was put instead, e.g. $.action.evidence - key paths only, never values. */
export function misplaced(p, missing) {
  const out = [];
  const walk = (n, at, depth) => {
    if (depth > 4) return;
    if (Array.isArray(n)) n.forEach((v, i) => walk(v, `${at}[${i}]`, depth + 1));
    else if (n && typeof n === "object") {
      for (const [k, v] of Object.entries(n)) {
        const here = `${at}.${k}`;
        if (depth > 0 && missing.includes(k)) out.push(here);
        walk(v, here, depth + 1);
      }
    }
  };
  walk(p, "$", 0);
  return out;
}

/** ['a', 'b'] - the same text in Java, Python and Node. */
export const pyList = (xs) => `[${xs.map((x) => `'${x}'`).join(", ")}]`;
const hasNonNull = (o, k) => has(o, k) && o[k] !== null;

/** Schema + per-action required fields. Throws ContractError - the agent loop sends it back for one fix-up. */
export function contract(p, schema = SCHEMA) {
  // Top level first, all at once: "missing 'exposed'" alone did not tell the model it had sent 'exposed_items'
  if (p && typeof p === "object" && !Array.isArray(p)) {
    const missing = (schema.required || []).filter((k) => !has(p, k));
    const extra = Object.keys(p).filter((k) => !has(schema.properties || {}, k));
    if (missing.length || extra.length) {
      const moved = misplaced(p, missing);
      throw new ContractError("$: " + (missing.length ? `missing ${pyList(missing)}` : "") + (missing.length && extra.length ? "; " : "")
        + (extra.length ? `unexpected ${pyList(extra)}` : "") + (moved.length ? ` (found at ${moved.join(", ")} - move it to the top level)` : "")
        + ` - the top-level keys are exactly ${pyList(schema.required || [])}`);
    }
  }
  validate(p, schema);
  const a = p.action || {};
  if (a.type === "post_customer_update") {
    if (!hasNonNull(a, "ticket_id") || !hasNonNull(a, "comment")) {
      throw new ContractError("$.action: post_customer_update needs ticket_id and comment");
    }
  } else if (has(a, "comment") || has(a, "ticket_id")) {
    throw new ContractError("$.action: action none takes no ticket_id or comment");
  }
  return p;
}

/** Every rule, against the SLA recomputed from source - not against what the agent says it saw. */
export function verify(p, sla) {
  const out = [];
  try { contract(p, SCHEMA); } catch (e) {
    if (!(e instanceof ContractError)) throw e;
    return new Verdict(false, [{ rule: "contract.invalid", detail: cut(e.message, 300) }]);
  }
  const claimed = new Set();
  for (const c of p.exposed) {
    const id = c.item;
    if (claimed.has(id)) { out.push({ rule: "claims.duplicate", detail: `${id} listed twice` }); continue; }
    claimed.add(id);
    const it = sla.item(id);
    if (!it || it.state === "ok") {
      out.push({ rule: "claims.unknown_item", detail: `${id} is not at_risk/breached in sla_report` + (it ? ` (it is ok, ${it.pct}% of target)` : "") });
      continue;
    }
    if (it.state !== c.state) out.push({ rule: "claims.wrong_state", detail: `${id}: claimed ${c.state}, actually ${it.state}` });
    const el = c.elapsed_minutes, tg = c.target_minutes;
    if (Math.abs(el - it.elapsedMinutes) > TOLERANCE_MIN || tg !== it.targetMinutes) {
      out.push({ rule: "claims.wrong_numbers", detail: `${id}: claimed ${el}/${tg} min, computed ${it.elapsedMinutes}/${it.targetMinutes}` });
    }
  }
  for (const it of sla.exposed()) {
    if (!claimed.has(it.id)) out.push({ rule: "claims.omitted", detail: `${it.id} is ${it.state} but not listed` });
  }
  const a = p.action;
  if (a.type === "post_customer_update") {
    const tid = a.ticket_id;
    const it = sla.item(tid);
    if (!sla.ticketIds.has(tid)) {
      out.push({ rule: "action.out_of_scope", detail: `${tid} is not a ticket of ${sla.accountId}` });
    } else if (!it || it.state === "ok") {
      out.push({ rule: "action.not_exposed", detail: `${tid} is ${!it ? "not tracked or not open" : `ok (${it.pct}% of target)`}`
        + " - a customer update needs an at_risk or breached ticket" });
    }
    out.push(...outbound(a.comment, sla));
  }
  return new Verdict(out.length === 0, out);
}

/** What may leave the building in a customer-visible comment. Also run by gate.apply() before the write. */
export function outbound(comment, sla) {
  const out = [];
  const c = comment == null ? "" : String(comment);
  if (c.trim().length < 40 || c.length > 700) out.push({ rule: "comment.length", detail: `${c.length} chars (40-700)` });
  if (redact(c, 0) !== c || SECRET_WORDS.test(c)) out.push({ rule: "comment.secret", detail: "secret-shaped text in a customer-visible comment" });
  const m = CONFIG_NAME.exec(c);
  if (m) out.push({ rule: "comment.internal_config", detail: `internal setting '${m[0]}' in a customer update` });
  for (const x of c.matchAll(ACCOUNT_ID)) {
    if (x[0] !== sla.accountId) { out.push({ rule: "comment.other_tenant", detail: `${x[0]} is another customer` }); break; }
  }
  for (const x of c.matchAll(OBJECT_ID)) {
    if (!sla.inScope(x[0])) { out.push({ rule: "comment.foreign_id", detail: `${x[0]} is not ${sla.accountId}'s` }); break; }
  }
  if (LINK.test(c)) out.push({ rule: "comment.link", detail: "customer updates carry no links" });
  return out;
}
