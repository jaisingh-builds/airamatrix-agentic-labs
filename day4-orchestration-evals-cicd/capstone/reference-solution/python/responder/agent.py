"""The single agent: a tool-use loop in plain code (Day 1's loop, grown up). Its controls:

* a turn limit and a budget cap, both checked BEFORE every model call; a failure reports what it cost
* structured output: submit_proposal's schema IS the contract, validated here; two fix-up rounds
* refuses to start while a write/admin token is in this process (least privilege per process)
* a Bedrock Guardrail intervention (AgentCore mode) ends the run - it is never retried around

`model` is any callable (messages, tools, system, max_tokens) -> Anthropic Messages response dict:
labkit's GatewayClient.messages in local mode, the Bedrock Converse adapter in the runtime, a fake in tests.
"""
import copy, os
from dataclasses import dataclass, field

from . import contracts
from .guardrails import contract, misplaced
from .repo import BudgetExceeded, BudgetGuard, spans
from .tools import SUBMIT, definitions, err
from .util import cut, jround

FORBIDDEN_ENV = ("AIRA_OPS_APPLY_TOKEN", "AIRA_OPS_TOKEN")
MAX_TOKENS = 3000
CONTRACT_RETRIES = 2       # as common's GatewayAgentRunner
MAX_RESULT_CHARS = 8000


@dataclass
class ToolCall:
    name: str
    input: dict
    ok: bool


@dataclass
class Result:
    proposal: dict
    cost_usd: float
    turns: int
    tool_calls: list


class RunError(Exception):
    """A run that produced no valid proposal.
    kind: budget | turns | contract | no_result | gateway | guardrail_intervened | forbidden_env"""

    def __init__(self, kind, msg, cost_usd, turns, calls):
        super().__init__(msg)
        self.kind, self.cost_usd, self.turns, self.tool_calls = kind, cost_usd, turns, list(calls)


class ModelError(Exception):
    """What a model client raises when the call fails (labkit's GatewayError has the same .status)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Telemetry:
    """A hook for the runtime's OpenTelemetry GenAI spans (execute_tool). Local mode: this no-op."""

    def tool(self, name, input_, call):
        return call()


class ResponderAgent:
    def __init__(self, model, pricing_model, max_turns, budget_usd, env=None, telemetry=None):
        self.model, self.pricing_model = model, pricing_model
        self.max_turns, self.budget_usd = max_turns, budget_usd
        self.env = env if env is not None else os.environ
        self.telemetry = telemetry or Telemetry()

    def run(self, system, prompt, tools, schema, tr):
        calls = []
        held = [k for k in FORBIDDEN_ENV if self.env.get(k)]
        if held:   # fail closed, before any cost
            raise RunError("forbidden_env", f"refusing to start the agent: {', '.join(held)} is set in this process. "
                           "A process that runs the agent holds no write or admin token - run `apply` in its own shell.",
                           0.0, 0, calls)
        tool_defs = definitions(schema)
        messages = [{"role": "user", "content": prompt}]
        budget = BudgetGuard(self.budget_usd, self.pricing_model)
        contract_errors = nudges = 0
        previous = None                        # the last rejected submission: a fix-up is merged onto it

        for turn in range(1, self.max_turns + 1):
            with tr.span("model.turn", turns=turn) as sp:
                try:
                    budget.check()                                     # refuse BEFORE spending
                except BudgetExceeded as e:
                    sp.fail(str(e))
                    raise RunError("budget", f"budget cap reached after {turn - 1} turns: {e}", budget.spent, turn - 1, calls)
                try:
                    resp = self.model(messages, tool_defs, system, MAX_TOKENS)
                except Exception as e:                                 # GatewayError / ModelError: status + message
                    if not hasattr(e, "status"):
                        raise
                    sp.fail(e)
                    raise RunError("gateway", f"model call failed: HTTP {e.status} {spans.redact(str(e), limit=None)}",
                                   budget.spent, turn - 1, calls)
                cost = budget.record(resp.get("usage") or {})
                stop = resp.get("stop_reason") or ""
                sp.set(cost_usd=jround(cost, 4), verdict=stop)
                if stop == "guardrail_intervened":
                    sp.fail("Bedrock Guardrail intervened")
                    raise RunError("guardrail_intervened", f"the Bedrock Guardrail intervened on turn {turn} - the run "
                                   "stops; nothing is proposed", budget.spent, turn, calls)
            content = resp.get("content") or []
            messages.append({"role": "assistant", "content": content})
            if resp.get("stop_reason") == "max_tokens":
                # Found live (Java): a reply cut off at max_tokens carried a submit_proposal with only 'summary'. A
                # truncated tool call is never validated or run - every tool_use gets an error result, and the model is told why.
                cut_off = [{"type": "tool_result", "tool_use_id": b.get("id", ""), "is_error": True,
                            "content": f"your reply was cut off at {MAX_TOKENS} tokens, so this call was not run. "
                                       f"Be brief (summary under 800 characters) and call {SUBMIT} again with all six keys."}
                           for b in content if b.get("type") == "tool_use"]
                tr.event("contract.rejected", reason="reply cut off at max_tokens - tool calls not run")
                contract_errors += 1
                if contract_errors > CONTRACT_RETRIES:
                    raise RunError("contract", f"no proposal matching the contract after {contract_errors} attempts",
                                   budget.spent, turn, calls)
                messages.append({"role": "user", "content": cut_off or f"Your reply was cut off. Be brief and call {SUBMIT}."})
                continue

            results, submitted = [], None
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                name, input_ = block.get("name", ""), block.get("input") or {}
                r = {"type": "tool_result", "tool_use_id": block.get("id", "")}
                results.append(r)
                if name == SUBMIT:
                    # Found live: after "missing ['evidence']" the model often resends ONLY the missing key. The candidate
                    # is the NEW submission plus only the schema-required top-level keys it leaves out, taken from the
                    # previous one - never a key the schema does not allow (found live in Node: an extra key sent once was
                    # carried forward and rejected three times). Validated in full: transport, not trust.
                    candidate = input_
                    if previous is not None and isinstance(input_, dict):
                        candidate = dict(input_)
                        for k in schema.get("required", []):
                            if k not in candidate and k in previous:
                                candidate[k] = previous[k]
                    try:
                        contract(candidate, schema)
                        submitted = candidate
                        r["content"] = "received"
                    except contracts.ContractError as e:
                        if isinstance(candidate, dict):
                            previous = copy.deepcopy(candidate)
                        contract_errors += 1
                        sent = list(input_) if isinstance(input_, dict) else []     # key names only - never the content
                        missing = [k for k in schema.get("required", []) if not isinstance(candidate, dict) or k not in candidate]
                        tr.event("contract.rejected", reason=cut(str(e), 200), kept=sent,
                                 dropped=misplaced(candidate, missing))
                        r.update(is_error=True, content=f"contract error: {e} - call {SUBMIT} again with the corrected "
                                 "keys (the keys you already sent are kept).")
                    continue
                with tr.span("tool", tool=name, input=input_) as ts:
                    res = self.telemetry.tool(name, input_, lambda: tools.call(name, input_))
                    ts.set(ok=not res.error)
                    if res.error:
                        ts.fail(cut(res.text, 200))
                calls.append(ToolCall(name, input_, not res.error))
                text = res.text if len(res.text) <= MAX_RESULT_CHARS else \
                    err("too_large", f"result over {MAX_RESULT_CHARS} chars - refused, not truncated").text
                r["content"] = text
                if res.error:
                    r["is_error"] = True
            if submitted is not None:
                return Result(submitted, budget.spent, turn, calls)
            if contract_errors > CONTRACT_RETRIES:
                raise RunError("contract", f"no proposal matching the contract after {contract_errors} attempts",
                               budget.spent, turn, calls)
            if results:
                messages.append({"role": "user", "content": results})
                continue
            nudges += 1
            if nudges > 1:
                raise RunError("no_result", f"the agent answered in text instead of calling {SUBMIT}", budget.spent, turn, calls)
            messages.append({"role": "user", "content": f"Call {SUBMIT} now with your proposal."})
        raise RunError("turns", f"turn limit {self.max_turns} reached without a proposal", budget.spent, self.max_turns, calls)
