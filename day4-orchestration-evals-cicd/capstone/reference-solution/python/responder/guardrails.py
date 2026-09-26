"""THE GUARDRAIL - in code, not in the prompt. A proposal reaches a human only if every rule passes;
a blocked proposal cannot be approved, even with an override (a guardrail a human can click past is a
warning). The same outbound rules run again inside gate.apply, at the point of the write.

rule                    refuses
contract.*              output that does not match sla-proposal.json, or an action missing its fields
claims.duplicate        an item listed twice in exposed
claims.unknown_item     an "exposed" item sla_report does not have (invented)
claims.wrong_state      at_risk vs breached wrong
claims.wrong_numbers    elapsed/target minutes not what the code computed (+-2 min)
claims.omitted          an at_risk/breached item left out (hiding exposure from the duty manager)
action.out_of_scope     a ticket that is not this account's (another tenant, or does not exist)
action.not_exposed      a customer update on a ticket that is not at_risk/breached
comment.length          under 40 or over 700 characters
comment.secret          anything secret-shaped: bearer/sk-/hex tokens, a secret env value, AIRA_OPS_* names
comment.internal_config an internal setting name (ingest.*, alerts.*, viewer.*, feature.*)
comment.other_tenant    another account's id (ACC-nnnn)
comment.foreign_id      a ticket or job id that is not this account's
comment.link            a URL - customer updates carry no links (a classic exfiltration channel)
"""
import re
from dataclasses import dataclass, field

from . import contracts
from .repo import spans
from .util import cut

TOLERANCE_MIN = 2
CONFIG_NAME = re.compile(r"\b(ingest|alerts|viewer|feature)\.[a-z_]+", re.I)
ACCOUNT_ID = re.compile(r"\bACC-\d{4}\b", re.ASCII)
OBJECT_ID = re.compile(r"\b[TJ]-\d{4}\b", re.ASCII)
LINK = re.compile(r"(?i)\b(https?://|www\.)")
SECRET_WORDS = re.compile(r"(?i)\b(AIRA_OPS_[A-Z_]+|ANTHROPIC_[A-Z_]+|bearer\s+\S{8,})")


@dataclass
class Verdict:
    passed: bool
    denials: list = field(default_factory=list)     # [{"rule", "detail"}]

    def rules(self):
        out = []
        for d in self.denials:
            if d["rule"] not in out:
                out.append(d["rule"])
        return out

    def to_json(self):
        return {"passed": self.passed, "denials": [dict(rule=d["rule"], detail=d["detail"]) for d in self.denials]}

    @staticmethod
    def from_json(n):
        n = n or {}
        return Verdict(bool(n.get("passed", False)),
                       [{"rule": d.get("rule", ""), "detail": d.get("detail", "")} for d in n.get("denials", [])])


def misplaced(p, missing):
    """Where a missing top-level key was put instead (e.g. $.action.evidence) - so the error can say so."""
    out = []

    def walk(n, path, depth):
        if depth > 4:
            return
        if isinstance(n, dict):
            for k, v in n.items():
                here = f"{path}.{k}"
                if depth > 0 and k in missing:
                    out.append(here)
                walk(v, here, depth + 1)
        elif isinstance(n, list):
            for i, v in enumerate(n):
                walk(v, f"{path}[{i}]", depth + 1)
    walk(p, "$", 0)
    return out


def py_list(xs):
    """['a', 'b'] - the same text in Java, Python and Node."""
    return "[" + ", ".join(f"'{x}'" for x in xs) + "]"


def denial(rule, detail):
    return {"rule": rule, "detail": detail}


def contract(p, schema=None):
    """Schema + per-action required fields. Raises ContractError - the agent loop sends it back for a fix-up."""
    schema = schema or contracts.SCHEMA
    # Top level first, all at once: "missing 'exposed'" alone did not tell the model it had sent 'exposed_items'
    if isinstance(p, dict):
        missing = [k for k in schema.get("required", []) if k not in p]
        extra = [k for k in p if k not in schema.get("properties", {})]
        if missing or extra:
            where = misplaced(p, missing)
            raise contracts.ContractError(
                "$: " + ("missing " + py_list(missing) if missing else "") + ("; " if missing and extra else "")
                + ("unexpected " + py_list(extra) if extra else "")
                + (f" (found at {', '.join(where)} - move it to the top level)" if where else "")
                + " - the top-level keys are exactly " + py_list(schema.get("required", [])))
    contracts.validate(p, schema)
    a = p.get("action", {})
    if a.get("type") == "post_customer_update":
        if a.get("ticket_id") is None or a.get("comment") is None:
            raise contracts.ContractError("$.action: post_customer_update needs ticket_id and comment")
    elif "comment" in a or "ticket_id" in a:
        raise contracts.ContractError("$.action: action none takes no ticket_id or comment")
    return p


def verify(p, sla):
    """Every rule, against the SLA recomputed from source - not against what the agent says it saw."""
    out = []
    try:
        contract(p)
    except contracts.ContractError as e:
        return Verdict(False, [denial("contract.invalid", cut(str(e), 300))])
    claimed = set()
    for c in p.get("exposed", []):
        id_ = c["item"]
        if id_ in claimed:
            out.append(denial("claims.duplicate", f"{id_} listed twice"))
            continue
        claimed.add(id_)
        it = sla.item(id_)
        if it is None or it.state == "ok":
            out.append(denial("claims.unknown_item", f"{id_} is not at_risk/breached in sla_report"
                              + ("" if it is None else f" (it is ok, {it.pct}% of target)")))
            continue
        if it.state != c["state"]:
            out.append(denial("claims.wrong_state", f"{id_}: claimed {c['state']}, actually {it.state}"))
        el, tg = c["elapsed_minutes"], c["target_minutes"]
        if abs(el - it.elapsed_minutes) > TOLERANCE_MIN or tg != it.target_minutes:
            out.append(denial("claims.wrong_numbers", f"{id_}: claimed {el}/{tg} min, computed "
                              f"{it.elapsed_minutes}/{it.target_minutes}"))
    for it in sla.exposed():
        if it.id not in claimed:
            out.append(denial("claims.omitted", f"{it.id} is {it.state} but not listed"))
    a = p["action"]
    if a["type"] == "post_customer_update":
        tid = a["ticket_id"]
        it = sla.item(tid)
        if tid not in sla.ticket_ids:
            out.append(denial("action.out_of_scope", f"{tid} is not a ticket of {sla.account_id}"))
        elif it is None or it.state == "ok":
            what = "not tracked or not open" if it is None else f"ok ({it.pct}% of target)"
            out.append(denial("action.not_exposed", f"{tid} is {what} - a customer update needs an at_risk or breached ticket"))
        out.extend(outbound(a["comment"], sla))
    return Verdict(not out, out)


def outbound(comment, sla):
    """What may leave the building in a customer-visible comment. Also run by gate.apply before the write."""
    out = []
    c = comment or ""
    if len(c.strip()) < 40 or len(c) > 700:
        out.append(denial("comment.length", f"{len(c)} chars (40-700)"))
    if spans.redact(c, limit=None) != c or SECRET_WORDS.search(c):
        out.append(denial("comment.secret", "secret-shaped text in a customer-visible comment"))
    m = CONFIG_NAME.search(c)
    if m:
        out.append(denial("comment.internal_config", f"internal setting '{m.group()}' in a customer update"))
    for m in ACCOUNT_ID.finditer(c):
        if m.group() != sla.account_id:
            out.append(denial("comment.other_tenant", f"{m.group()} is another customer"))
            break
    for m in OBJECT_ID.finditer(c):
        if not sla.in_scope(m.group()):
            out.append(denial("comment.foreign_id", f"{m.group()} is not {sla.account_id}'s"))
            break
    if LINK.search(c):
        out.append(denial("comment.link", "customer updates carry no links"))
    return out
