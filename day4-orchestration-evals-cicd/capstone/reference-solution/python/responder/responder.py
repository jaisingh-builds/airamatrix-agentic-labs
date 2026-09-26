"""One run, the same in both modes: agent -> guardrail (against the SLA recomputed from source) -> a status
that says whether a human has something to decide. Used by the local CLI, the eval harness and the
AgentCore runtime's /invocations.

  failed | guardrail_intervened   the agent produced no proposal (budget, turns, contract, Bedrock Guardrail)
  blocked                         the code guardrail refused the proposal - a human cannot approve it
  no_action                       nothing to post (nothing exposed, or no exposed ticket)
  awaiting_approval               a customer update is waiting for a named human with a reason
"""
from dataclasses import dataclass, field

from . import guardrails, prompts, sla as sla_mod
from .agent import RunError
from .contracts import SCHEMA
from .tools import Tools
from .util import iso, jround


@dataclass
class Outcome:
    run_id: str
    status: str
    proposal: object
    verdict: object
    sla: object
    tool_calls: list = field(default_factory=list)
    cost_usd: float = 0.0
    turns: int = 0
    error: object = None

    def trajectory(self):
        return [[c.name, c.input, c.ok] for c in self.tool_calls]

    def to_json(self):
        o = {"run_id": self.run_id, "status": self.status, "cost_usd": jround(self.cost_usd, 4),
             "turns": self.turns, "tool_calls": len(self.tool_calls)}
        if self.proposal is not None:
            o["proposal"] = self.proposal
        if self.verdict is not None:
            o["verdict"] = self.verdict.to_json()
        if self.sla is not None:
            o["sla"] = self.sla.to_json()
        o["trajectory"] = self.trajectory()
        if self.error is not None:
            o["error"] = self.error
        return o


def run(run_id, account_id, as_of, question, ops, agent, tr):
    with tr.span("run", account=account_id, stage="sla-responder") as root:
        try:
            r = agent.run(prompts.SYSTEM, prompts.task(account_id, iso(as_of), question), Tools(ops, account_id, as_of),
                          SCHEMA, tr)
        except RunError as e:
            status = "guardrail_intervened" if e.kind == "guardrail_intervened" else "failed"
            root.set(cost_usd=jround(e.cost_usd, 4), turns=e.turns, verdict=status)
            root.fail(f"{e.kind}: {e}")
            return Outcome(run_id, status, None, None, None, e.tool_calls, e.cost_usd, e.turns, f"{e.kind}: {e}")
        root.set(cost_usd=jround(r.cost_usd, 4), turns=r.turns, tool_calls=len(r.tool_calls))

        with tr.span("guardrail.verify", stage="code-guardrail") as vs:
            try:
                sla = sla_mod.compute(ops, account_id, as_of)     # source of truth, recomputed - not what the agent saw
            except Exception as e:                                # cannot verify -> fail closed
                vs.fail(f"{type(e).__name__}: {e}")
                root.fail(f"verification could not run: {e}")
                return Outcome(run_id, "failed", r.proposal, None, None, r.tool_calls, r.cost_usd, r.turns,
                               f"verification could not run: {e}")
            v = guardrails.verify(r.proposal, sla)
            vs.set(verdict="pass" if v.passed else "block", denials=v.rules(), kept=len(sla.exposed()))
            if not v.passed:
                vs.fail("guardrail refused: " + ", ".join(v.rules()))
        action = r.proposal["action"]["type"]
        status = "blocked" if not v.passed else "no_action" if action == "none" else "awaiting_approval"
        root.set(verdict=status, action=action)
        if status == "awaiting_approval":
            tr.event("gate.waiting", action=action, input={"ticket_id": r.proposal["action"].get("ticket_id", "")})
        return Outcome(run_id, status, r.proposal, v, sla, r.tool_calls, r.cost_usd, r.turns, None)


def save(store, o, trace):
    """Persist an outcome (local runs, AgentCore invocations, replays)."""
    store.finish_run(o.run_id, o.status, o.cost_usd, o.turns, len(o.tool_calls), o.error, trace)
    if o.proposal is not None:
        store.save_proposal(o.run_id, o.proposal, o.sla.to_json() if o.sla else None,
                            o.verdict.to_json() if o.verdict else None, o.trajectory())
