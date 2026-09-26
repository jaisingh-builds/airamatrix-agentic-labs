"""The SLA arithmetic - done in CODE, never by the model. The agent reads the result through the
sla_report tool, and the guardrail recomputes it from source to check every number the agent claims.

Policy (the contract's SLA, in one place a reviewer can read):
  ticket target = contract_sla_minutes x {P1: 1, P2: 2, P3: 5}; P4 is not tracked
  job target    = contract_sla_minutes (turnaround), for queued and running jobs
  state         = breached if elapsed > target, at_risk if elapsed >= 75% of target, else ok
  the clock     = an explicit as_of instant, so the same data always gives the same answer
                  (the seed data is from 24 Sep 2026; evals and demos freeze the clock there)
Anything created after as_of did not exist yet and is left out.
"""
from dataclasses import dataclass, field

from .ops import OpsError
from .util import cut, iso, jround, minutes_between, parse_instant

AT_RISK = 0.75
PRIORITY_MULTIPLIER = {"P1": 1, "P2": 2, "P3": 5}
OPEN_TICKET = ("open", "in_progress")
ACTIVE_JOB = ("queued", "running")
MAX_TICKETS = 25
POLICY = ("ticket target = contract_sla_minutes x {P1:1, P2:2, P3:5}, P4 untracked; job target = "
          "contract_sla_minutes; breached > 100%, at_risk >= 75%")


@dataclass
class Item:
    id: str
    kind: str
    priority: object
    status: str
    title: object
    started_at: str
    elapsed_minutes: int
    target_minutes: int
    pct: int
    state: str
    slide_count: object = None


@dataclass
class Report:
    account_id: str
    account_name: str
    tier: str
    contract_sla_minutes: int
    as_of: str
    items: list
    untracked: list
    ticket_ids: list = field(default_factory=list)
    job_ids: list = field(default_factory=list)

    def exposed(self):
        """Items at risk or breached, most urgent first."""
        return [i for i in self.items if i.state != "ok"]

    def item(self, id_):
        return next((i for i in self.items if i.id == id_), None)

    def in_scope(self, id_):
        return id_ in self.ticket_ids or id_ in self.job_ids

    def to_json(self):
        items = []
        for i in self.items:
            n = {"item": i.id, "kind": i.kind, "status": i.status, "started_at": i.started_at,
                 "elapsed_minutes": i.elapsed_minutes, "target_minutes": i.target_minutes,
                 "pct_of_target": i.pct, "state": i.state}
            if i.priority is not None:
                n["priority"] = i.priority
            if i.title is not None:
                n["title"] = i.title
            if i.slide_count is not None:
                n["slide_count"] = i.slide_count
            items.append(n)
        return {"account_id": self.account_id, "account_name": self.account_name, "tier": self.tier,
                "contract_sla_minutes": self.contract_sla_minutes, "as_of": self.as_of, "items": items,
                "untracked": list(self.untracked), "account_ticket_ids": list(self.ticket_ids),
                "account_job_ids": list(self.job_ids), "policy": POLICY}


def from_json(o):
    """The snapshot stored with a proposal - what the human saw, and what apply re-checks the comment against."""
    items = [Item(n.get("item", ""), n.get("kind", ""), n.get("priority"), n.get("status", ""), n.get("title"),
                  n.get("started_at", ""), int(n.get("elapsed_minutes", 0)), int(n.get("target_minutes", 0)),
                  int(n.get("pct_of_target", 0)), n.get("state", ""), n.get("slide_count"))
             for n in o.get("items", [])]
    return Report(o.get("account_id", ""), o.get("account_name", ""), o.get("tier", ""),
                  int(o.get("contract_sla_minutes", 0)), o.get("as_of", ""), items, list(o.get("untracked", [])),
                  list(o.get("account_ticket_ids", [])), list(o.get("account_job_ids", [])))


def state(elapsed, target):
    if elapsed > target:
        return "breached"
    return "at_risk" if elapsed >= AT_RISK * target else "ok"


def compute(ops, account_id, as_of):
    """Reads the account, its tickets (each one, for created_at) and its jobs, and applies the policy at as_of."""
    acc = ops.account(account_id)
    sla = int(acc.get("contract_sla_minutes") or 0)
    if sla <= 0:
        raise OpsError(502, "invalid", f"account {account_id} has no contract_sla_minutes")
    items, untracked, ticket_ids, job_ids = [], [], [], []

    read = 0
    for t in ops.tickets(account_id).get("tickets", []):
        if t.get("account_id", account_id) != account_id:        # the tenant boundary, in code too
            continue
        tid = t.get("id", "")
        if tid not in ticket_ids:
            ticket_ids.append(tid)
        if t.get("status") not in OPEN_TICKET or read >= MAX_TICKETS:
            continue
        full = ops.ticket(tid)
        read += 1
        created = parse_instant(full.get("created_at"))
        if created > as_of:                                        # did not exist yet
            ticket_ids.remove(tid)
            continue
        mult = PRIORITY_MULTIPLIER.get(full.get("priority"))
        if mult is None:
            untracked.append(tid)
            continue
        target = sla * mult
        elapsed = minutes_between(created, as_of)
        items.append(Item(tid, "ticket", full.get("priority"), full.get("status", ""), cut(full.get("title", ""), 120),
                          iso(created), elapsed, target, jround(100.0 * elapsed / target), state(elapsed, target)))
    for j in ops.jobs(account_id).get("jobs", []):
        if j.get("account_id", account_id) != account_id:
            continue
        jid = j.get("id", "")
        sub = parse_instant(j.get("submitted_at"))
        if sub > as_of:
            continue
        if jid not in job_ids:
            job_ids.append(jid)
        if j.get("status") not in ACTIVE_JOB:
            continue
        elapsed = minutes_between(sub, as_of)
        items.append(Item(jid, "job", None, j.get("status", ""), None, iso(sub), elapsed, sla,
                          jround(100.0 * elapsed / sla), state(elapsed, sla), int(j.get("slide_count") or 0)))
    items.sort(key=lambda i: (-i.pct, i.id))
    return Report(account_id, acc.get("name", ""), acc.get("tier", ""), sla, iso(as_of), items, untracked,
                  ticket_ids, job_ids)
