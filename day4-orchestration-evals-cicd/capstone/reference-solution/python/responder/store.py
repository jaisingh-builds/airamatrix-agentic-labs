"""Persisted state, SQLite (the Lab 5.1 store's shape) - the same tables as the Java and Node solutions,
so one language can read another's database. Every hand-off is a row: the run, the proposal with its SLA
snapshot and guardrail verdict, the HUMAN decision (who, why, the hash of what they saw), and the operation
id of the one write - stored before it is sent.

Run statuses, forward only:
  failed | guardrail_intervened | no_action | blocked | awaiting_approval -> approved | rejected
  approved -> applied | apply_failed | outcome_unknown (apply again: same operation id)
"""
import hashlib, json, secrets, sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .util import ArgError, SetupError, canonical, cut, dumps

IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA = [
    "create table if not exists runs(id text primary key, account_id text, as_of text, question text, mode text, "
    "status text, cost_usd real, turns integer, tool_calls integer, error text, trace text, created_at text, updated_at text)",
    "create table if not exists proposals(run_id text primary key, proposal text, sla text, verdict text, sha text, trajectory text)",
    "create table if not exists approvals(run_id text primary key, decision text, approver text, principal text, "
    "reason text, at text, proposal_sha text)",
    "create table if not exists operations(run_id text primary key, op_id text, action text, payload text, status text, "
    "response text, created_at text, updated_at text)",
]


@dataclass
class Run:
    id: str
    account_id: str
    as_of: str
    question: object
    mode: str
    status: str
    cost_usd: float
    turns: int
    tool_calls: int
    error: object
    trace: object
    created_at: str


@dataclass
class ProposalRow:
    proposal: dict
    sla: object
    verdict: object
    sha: str
    trajectory: object


@dataclass
class Approval:
    decision: str
    approver: str
    principal: str
    reason: str
    at: str
    proposal_sha: str


@dataclass
class Operation:
    op_id: str
    action: str
    payload: object
    status: str
    response: object


class Conflict(Exception):
    """Two writers raced for a row only one may create (two people deciding one run)."""


def now():
    return datetime.now(IST).isoformat(timespec="seconds")


def new_id():
    return secrets.token_hex(5)


def sha(proposal):
    """Fingerprint of the exact proposal a human decides on (canonical JSON: sorted keys, no whitespace)."""
    return hashlib.sha256(canonical(proposal).encode("utf-8")).hexdigest()


def _j(s):
    return None if s is None else json.loads(s)


def _s(v):
    return None if v is None else dumps(v)


class Store:
    def __init__(self, path):
        try:
            self.db = sqlite3.connect(path)
            for sql in SCHEMA:
                self.db.execute(sql)
            self.db.commit()
        except sqlite3.Error as e:
            raise SetupError(f"cannot open {path}: {e}") from None

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _exec(self, sql, *args):
        self.db.execute(sql, args)
        self.db.commit()

    def create_run(self, id_, account_id, as_of, question, mode):
        self._exec("insert into runs(id, account_id, as_of, question, mode, status, cost_usd, turns, tool_calls, "
                   "created_at, updated_at) values(?,?,?,?,?,?,0,0,0,?,?)", id_, account_id, as_of, question, mode,
                   "created", now(), now())
        return id_

    def finish_run(self, id_, status, cost, turns, tool_calls, error, trace):
        self._exec("update runs set status=?, cost_usd=?, turns=?, tool_calls=?, error=?, trace=?, updated_at=? where id=?",
                   status, cost, turns, tool_calls, error, trace, now(), id_)

    def set_status(self, id_, status):
        self._exec("update runs set status=?, updated_at=? where id=?", status, now(), id_)

    def _runs(self, sql, *args):
        cols = "id, account_id, as_of, question, mode, status, cost_usd, turns, tool_calls, error, trace, created_at"
        rows = self.db.execute(f"select {cols} from runs " + sql, args).fetchall()
        return [Run(r[0], r[1], r[2], r[3], r[4], r[5], float(r[6] or 0), int(r[7] or 0), int(r[8] or 0), r[9], r[10], r[11])
                for r in rows]

    def run(self, id_):
        r = self._runs("where id=?", id_)
        if not r:
            raise ArgError(f"no run {id_}")
        return r[0]

    def runs(self, limit):
        return self._runs("order by created_at desc, rowid desc limit ?", limit)

    def save_proposal(self, id_, proposal, sla, verdict, trajectory):
        self._exec("insert or replace into proposals values(?,?,?,?,?,?)", id_, _s(proposal), _s(sla), _s(verdict),
                   sha(proposal), _s(trajectory))

    def proposal(self, id_):
        r = self.db.execute("select proposal, sla, verdict, sha, trajectory from proposals where run_id=?", (id_,)).fetchone()
        return None if r is None else ProposalRow(_j(r[0]), _j(r[1]), _j(r[2]), r[3], _j(r[4]))

    def record_decision(self, id_, decision, approver, principal, reason, proposal_sha):
        """The decision is bound to the proposal as the human saw it. The primary key is the real 'one decision' guarantee."""
        try:
            self._exec("insert into approvals values(?,?,?,?,?,?,?)", id_, decision, approver, principal, reason, now(),
                       proposal_sha)
        except sqlite3.IntegrityError:
            raise Conflict(f"run {id_} was already decided") from None

    def approval(self, id_):
        r = self.db.execute("select decision, approver, principal, reason, at, proposal_sha from approvals where run_id=?",
                            (id_,)).fetchone()
        return None if r is None else Approval(*r)

    def record_operation(self, id_, op_id, action, payload):
        self._exec("insert into operations values(?,?,?,?,?,?,?,?)", id_, op_id, action, _s(payload), "pending", None,
                   now(), now())

    def operation_result(self, id_, status, response):
        self._exec("update operations set status=?, response=?, updated_at=? where run_id=?", status, cut(response, 4000),
                   now(), id_)

    def operation(self, id_):
        r = self.db.execute("select op_id, action, payload, status, response from operations where run_id=?", (id_,)).fetchone()
        return None if r is None else Operation(r[0], r[1], _j(r[2]), r[3], r[4])
