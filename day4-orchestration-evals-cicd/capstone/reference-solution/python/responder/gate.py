"""THE HUMAN APPROVAL POINT and the one write.

decide(): a named person, a reason, one decision per run, bound to the hash of the proposal they saw.
          Refused: no name, no reason (or a one-word one), an agent identity as approver, a run that is not
          waiting, a run the guardrail blocked (no override exists), a second decision.
apply():  plain code, not an agent, in its own process with the only write credential. Checks the DECISION
          RECORD (not the status field), the proposal hash, the write host, the outbound guardrail again and the
          ticket's current state; stores the operation id BEFORE sending so a retry writes at most once.
"""
import json, os, re, urllib.error, urllib.parse, urllib.request, uuid

from . import guardrails, sla as sla_mod
from .repo import spans
from .store import Conflict, sha
from .util import ArgError, cut, dumps


class GateError(Exception):
    pass


# Names an agent or service identity uses. An agent cannot approve its own proposal.
AGENT_IDENTITIES = {"sla-responder", "capstone-agent", "investigator", "reviewer", "supervisor", "pipeline-agents",
                    "pipeline-apply"}
AGENTISH = re.compile(r"(?i)(^|[^a-z])(agent|bot|runtime|claude|llm)([^a-z]|$)")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def decide(store, rid, decision, approver, principal, reason, tr):
    try:
        return _decide(store, rid, decision, approver, principal, reason, tr)
    except GateError as e:
        refused(tr, decision, e)
        raise


def refused(tr, what, e):
    """A refusal is part of the story of a run: it goes in the trace too (our message, never the human's reason)."""
    tr.event("gate.refused", decision=what, reason=cut(str(e), 200))


def _decide(store, rid, decision, approver, principal, reason, tr):
    if decision not in ("approve", "reject"):
        raise ArgError("decision must be approve or reject")
    who, why = (approver or "").strip(), (reason or "").strip()
    if not who or not why:
        raise GateError("a decision needs --by (who) and --reason (why)")
    if len(why) < 10 or " " not in why:
        raise GateError(f"--reason must say why in a sentence, not '{why}'")
    if who.lower() in AGENT_IDENTITIES or AGENTISH.search(who):
        raise GateError(f"'{who}' is an agent or service identity - a person decides, not the agent that proposed it")
    r = store.run(rid)
    if store.approval(rid) is not None:
        raise GateError(f"run {rid} was already decided")
    if r.status == "blocked":
        rules = guardrails.Verdict.from_json(store.proposal(rid).verdict).rules()
        raise GateError(f"the guardrail blocked this proposal ({', '.join(rules)}). "
                        "There is no override: fix the cause and run again.")
    if r.status != "awaiting_approval":
        raise GateError(f"run {rid} is {r.status}, not waiting for a decision")
    p = store.proposal(rid)
    try:
        store.record_decision(rid, decision, who, principal, why, p.sha)
    except Conflict as e:
        raise GateError(str(e)) from None
    store.set_status(rid, "approved" if decision == "approve" else "rejected")
    tr.event("gate.decided", decision=decision, approver=who)    # the reason stays in the store, not the trace
    return store.run(rid)


def apply(store, rid, writer, ops_url, tr):
    """writer: what apply needs from aira-ops with the WRITE credential - get_ticket(tid) and
    post_comment(tid, comment, key), each returning (status, body). Tests pass a fake; real: HttpOpsWriter."""
    try:
        return _apply(store, rid, writer, ops_url, tr)
    except GateError as e:
        refused(tr, "apply", e)
        raise


def _apply(store, rid, writer, ops_url, tr):
    r = store.run(rid)
    a = store.approval(rid)
    if a is None or a.decision != "approve":
        raise GateError(f"run {rid} has no approval on record")
    p = store.proposal(rid)
    if p is None or sha(p.proposal) != a.proposal_sha:
        raise GateError(f"run {rid}: the proposal changed after it was decided - it needs a new decision")
    if r.status == "applied":                                   # applying again is a no-op, not an error
        return r
    if r.status not in ("approved", "outcome_unknown"):
        raise GateError(f"run {rid} is {r.status}; only an approved run can be applied")
    if r.mode != "local":
        raise GateError(f"run {rid} read the SHARED aira-ops through the AgentCore Gateway. The decision is recorded; "
                        "the write is made in local mode only (classroom rule: nobody writes to the shared aira-ops)")
    host = urllib.parse.urlsplit(ops_url).hostname or ""
    host = f"[{host}]" if ":" in host else host
    allowed = os.environ.get("CAPSTONE_ALLOW_WRITE_HOST")
    if host not in LOCAL_HOSTS and host != allowed:
        raise GateError(f"refusing to write to {host}: apply writes only to your own aira-ops on this machine "
                        "(set CAPSTONE_ALLOW_WRITE_HOST to that host name to allow another)")
    action = p.proposal.get("action", {})
    if action.get("type") != "post_customer_update":
        raise GateError(f"run {rid} has nothing to apply")
    tid, comment = action.get("ticket_id", ""), action.get("comment", "")
    again = guardrails.outbound(comment, sla_mod.from_json(p.sla))    # defence in depth, at the write
    if again:
        raise GateError(f"outbound guardrail refused the comment: {again[0]['rule']}")

    op = store.operation(rid)
    if op is None:                                              # stored BEFORE the request is sent
        store.record_operation(rid, str(uuid.uuid4()), "post_customer_update", {"ticket_id": tid, "comment": comment})
        op = store.operation(rid)
    if op.status == "done":
        store.set_status(rid, "applied")
        return store.run(rid)

    with tr.span("apply", action="post_customer_update", op_id=op.op_id, approver=a.approver,
                 input={"ticket_id": tid}) as sp:
        st, body = writer.get_ticket(tid)                       # the world may have moved while the human decided
        if st == 404:
            sp.fail("not visible to the write credential")
            raise GateError(f"{tid} is not visible to the apply credential")
        now_status = body.get("status", "") if isinstance(body, dict) else ""
        if st == 200 and now_status not in sla_mod.OPEN_TICKET:
            sp.fail(f"stale: ticket is {now_status}")
            raise GateError(f"{tid} is {now_status} now - the update is stale; nothing was written")
        st, body = writer.post_comment(tid, comment, op.op_id)
        sp.set(http_status=st, replayed=bool(body.get("_replayed", False)) if isinstance(body, dict) else False)
        if st in (200, 201):
            store.operation_result(rid, "done", f"HTTP {st}")
            store.set_status(rid, "applied")
        elif st == 0 or st >= 500:
            store.operation_result(rid, "pending", dumps(body))
            store.set_status(rid, "outcome_unknown")
            sp.fail("outcome unknown - run apply again; the same operation id makes it safe")
        else:
            store.operation_result(rid, "failed", dumps(body))
            store.set_status(rid, "apply_failed")
            sp.fail(f"HTTP {st}")
    return store.run(rid)


class HttpOpsWriter:
    """aira-ops with the apply token: one read (current state) and one idempotent comment."""

    def __init__(self, base, token, timeout=8.0):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout

    def get_ticket(self, tid):
        return self._send(urllib.request.Request(f"{self.base}/tickets/{urllib.parse.quote(tid, safe='')}"))

    def post_comment(self, tid, comment, key):
        return self._send(urllib.request.Request(
            f"{self.base}/tickets/{urllib.parse.quote(tid, safe='')}/comments", method="POST",
            data=dumps({"body": comment}).encode(), headers={"Content-Type": "application/json", "Idempotency-Key": key}))

    def _send(self, req):
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            try:
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                status = resp.status
            except urllib.error.HTTPError as e:
                resp, status = e, e.code
            with resp:
                raw = resp.read()
            return status, (json.loads(raw) if raw else {})
        except (urllib.error.URLError, OSError, ValueError) as e:
            return 0, {"error": spans.redact(type(e).__name__)}
