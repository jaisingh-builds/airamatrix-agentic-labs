// The SLA arithmetic - done in CODE, never by the model. The agent reads the result through the
// sla_report tool, and the guardrail recomputes it from source to check every number the agent claims.
//
// Policy (the contract's SLA, in one place a reviewer can read):
//   ticket target = contract_sla_minutes x {P1: 1, P2: 2, P3: 5}; P4 is not tracked
//   job target    = contract_sla_minutes (turnaround), for queued and running jobs
//   state         = breached if elapsed > target, at_risk if elapsed >= 75% of target, else ok
//   the clock     = an explicit as_of instant, so the same data always gives the same answer
//                   (the seed data is from 24 Sep 2026; evals and demos freeze the clock there)
// Anything created after as_of did not exist yet and is left out.
import { OpsError } from "./ops.mjs";
import { minutesBetween, parseInstant } from "./time.mjs";

export const AT_RISK = 0.75;
export const PRIORITY_MULTIPLIER = { P1: 1, P2: 2, P3: 5 };
export const OPEN_TICKET = new Set(["open", "in_progress"]);
export const ACTIVE_JOB = new Set(["queued", "running"]);
export const MAX_TICKETS = 25;
export const POLICY = "ticket target = contract_sla_minutes x {P1:1, P2:2, P3:5}, P4 untracked; job target = "
  + "contract_sla_minutes; breached > 100%, at_risk >= 75%";

export const cut = (s, n) => {
  s = s == null ? "" : String(s);
  return s.length <= n ? s : `${s.slice(0, n)}...[+${s.length - n} chars]`;
};

export function state(elapsed, target) {
  if (elapsed > target) return "breached";
  return elapsed >= AT_RISK * target ? "at_risk" : "ok";
}

export class Report {
  constructor(o) { Object.assign(this, o); }   // accountId, accountName, tier, contractSlaMinutes, asOf, items, untracked, ticketIds, jobIds

  /** Items at risk or breached, most urgent first. */
  exposed() { return this.items.filter((i) => i.state !== "ok"); }
  item(id) { return this.items.find((i) => i.id === id) || null; }
  inScope(id) { return this.ticketIds.has(id) || this.jobIds.has(id); }

  toJson() {
    const items = this.items.map((i) => {
      const n = { item: i.id, kind: i.kind, status: i.status, started_at: i.startedAt, elapsed_minutes: i.elapsedMinutes,
        target_minutes: i.targetMinutes, pct_of_target: i.pct, state: i.state };
      if (i.priority != null) n.priority = i.priority;
      if (i.title != null) n.title = i.title;
      if (i.slideCount != null) n.slide_count = i.slideCount;
      return n;
    });
    return { account_id: this.accountId, account_name: this.accountName, tier: this.tier,
      contract_sla_minutes: this.contractSlaMinutes, as_of: this.asOf, items, untracked: [...this.untracked],
      account_ticket_ids: [...this.ticketIds], account_job_ids: [...this.jobIds], policy: POLICY };
  }

  /** The snapshot stored with a proposal - what the human saw, and what apply re-checks the comment against. */
  static fromJson(o) {
    return new Report({
      accountId: o.account_id ?? "", accountName: o.account_name ?? "", tier: o.tier ?? "",
      contractSlaMinutes: o.contract_sla_minutes ?? 0, asOf: o.as_of ?? "",
      items: (o.items || []).map((n) => ({ id: n.item, kind: n.kind, priority: n.priority ?? null, status: n.status,
        title: n.title ?? null, startedAt: n.started_at, elapsedMinutes: n.elapsed_minutes, targetMinutes: n.target_minutes,
        pct: n.pct_of_target, state: n.state, slideCount: n.slide_count ?? null })),
      untracked: o.untracked || [], ticketIds: new Set(o.account_ticket_ids || []), jobIds: new Set(o.account_job_ids || []),
    });
  }
}

/** Reads the account, its tickets (each one, for created_at) and its jobs, and applies the policy at asOf. */
export async function compute(ops, accountId, asOf) {
  const acc = await ops.account(accountId);
  const sla = Number(acc.contract_sla_minutes || 0);
  if (!(sla > 0)) throw new OpsError(502, "invalid", `account ${accountId} has no contract_sla_minutes`);
  const items = [], untracked = [], ticketIds = new Set(), jobIds = new Set();

  let read = 0;
  for (const t of (await ops.tickets(accountId)).tickets || []) {
    if ((t.account_id ?? accountId) !== accountId) continue;          // the tenant boundary, in code too
    const id = t.id;
    ticketIds.add(id);
    if (!OPEN_TICKET.has(t.status) || read >= MAX_TICKETS) continue;
    const full = await ops.ticket(id);
    read++;
    const created = parseInstant(full.created_at);
    if (created.isAfter(asOf)) { ticketIds.delete(id); continue; }    // did not exist yet
    const mult = PRIORITY_MULTIPLIER[full.priority];
    if (mult == null) { untracked.push(id); continue; }
    const target = sla * mult;
    const elapsed = minutesBetween(created, asOf);
    items.push({ id, kind: "ticket", priority: full.priority, status: full.status, title: cut(full.title, 120),
      startedAt: created.toString(), elapsedMinutes: elapsed, targetMinutes: target,
      pct: Math.round((100 * elapsed) / target), state: state(elapsed, target), slideCount: null });
  }
  for (const j of (await ops.jobs(accountId)).jobs || []) {
    if ((j.account_id ?? accountId) !== accountId) continue;
    const sub = parseInstant(j.submitted_at);
    if (sub.isAfter(asOf)) continue;
    jobIds.add(j.id);
    if (!ACTIVE_JOB.has(j.status)) continue;
    const elapsed = minutesBetween(sub, asOf);
    items.push({ id: j.id, kind: "job", priority: null, status: j.status, title: null, startedAt: sub.toString(),
      elapsedMinutes: elapsed, targetMinutes: sla, pct: Math.round((100 * elapsed) / sla), state: state(elapsed, sla),
      slideCount: Number(j.slide_count || 0) });
  }
  items.sort((a, b) => b.pct - a.pct || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return new Report({ accountId, accountName: acc.name ?? "", tier: acc.tier ?? "", contractSlaMinutes: sla,
    asOf: asOf.toString(), items, untracked, ticketIds, jobIds });
}
