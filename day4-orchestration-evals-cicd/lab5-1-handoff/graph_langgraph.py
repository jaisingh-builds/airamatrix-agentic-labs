#!/usr/bin/env python3
"""
The same pipeline, as a LangGraph graph (pip install langgraph).

    python3 graph_langgraph.py --account ACC-1001 --question "Ingest backlog on T-1001"

What the graph buys you, compared with pipeline.py:
  * the flow is DATA: nodes, edges and a conditional route you can print and test
  * checkpointing after every node, keyed by thread_id, for free
  * interrupt(): the graph pauses at the gate, and resumes with the human's answer

What it costs: a dependency, its vocabulary, and state that now lives inside the
framework's checkpointer instead of tables you designed. The agents, the
contracts and the apply step are the SAME functions - the framework only moves
the arrows. Here the checkpointer is in memory, so the pause and the resume
must happen in one process; pipeline.py's SQLite store survives restarts.
"""
import argparse, json, os, sys, uuid
from pathlib import Path
from typing import TypedDict, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
from langgraph.graph import StateGraph, START, END          # noqa: E402
from langgraph.types import interrupt, Command               # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver        # noqa: E402
import agents, pipeline                                      # noqa: E402
from contracts import PROPOSAL, VERDICT, validate, check_change  # noqa: E402

class State(TypedDict, total=False):
    account_id: str
    question: str
    proposal: dict
    verdict: dict
    decision: dict
    op_id: str
    outcome: str
    cost_usd: float

def build(runner, ops_url, write_token):
    def investigate(s: State):
        r = runner.run("investigate", agents.INVESTIGATE_SYSTEM, agents.investigate_prompt(s["account_id"], s["question"]), PROPOSAL)
        check_change(validate(r.output, PROPOSAL)["proposed_change"])
        return {"proposal": r.output, "cost_usd": s.get("cost_usd", 0) + r.cost_usd}

    def review(s: State):
        r = runner.run("review", agents.REVIEW_SYSTEM, agents.review_prompt(s["account_id"], s["question"], s["proposal"]), VERDICT)
        return {"verdict": validate(r.output, VERDICT), "cost_usd": s.get("cost_usd", 0) + r.cost_usd}

    def gate(s: State):
        # The graph stops HERE and is checkpointed. invoke() returns; a human answers;
        # invoke(Command(resume=answer)) continues from this exact line.
        answer = interrupt({"proposal": s["proposal"], "verdict": s["verdict"]})
        if not (answer.get("by") or "").strip() or not (answer.get("reason") or "").strip():
            raise pipeline.GateError("a decision needs an approver name and a reason")
        if s["verdict"]["verdict"] == "block" and answer["decision"] == "approve" and not answer.get("override"):
            raise pipeline.GateError("the reviewer blocked this proposal; approving it needs override")
        # LangGraph checkpoints BETWEEN nodes, not inside them. An op id minted inside apply()
        # would be lost if apply crashed after sending; minted here, it is saved before apply runs.
        return {"decision": answer, "op_id": s.get("op_id") or str(uuid.uuid4())}

    def apply(s: State):
        op_id = s["op_id"]                                 # from the checkpoint - same id on every retry
        method, path, body = pipeline.request_for(check_change(dict(s["proposal"]["proposed_change"])))
        status, resp = pipeline.http(method, ops_url + path, body, write_token, op_id)
        outcome = "applied" if status in (200, 201) else "outcome_unknown" if status == 0 or status >= 500 else "apply_failed"
        return {"op_id": op_id, "outcome": outcome}

    def after_investigate(s: State):
        return "done" if s["proposal"]["proposed_change"]["action"] == "none" else "review"

    def after_gate(s: State):
        return "apply" if s["decision"]["decision"] == "approve" else "done"

    g = StateGraph(State)
    g.add_node("investigate", investigate); g.add_node("review", review)
    g.add_node("gate", gate); g.add_node("apply", apply)
    g.add_edge(START, "investigate")
    g.add_conditional_edges("investigate", after_investigate, {"review": "review", "done": END})
    g.add_edge("review", "gate")
    g.add_conditional_edges("gate", after_gate, {"apply": "apply", "done": END})
    g.add_edge("apply", END)
    return g.compile(checkpointer=InMemorySaver())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account"); ap.add_argument("--question")
    ap.add_argument("--print-graph", action="store_true")
    a = ap.parse_args()
    if not a.print_graph and not (a.account and a.question):
        ap.error("--account and --question are required (or --print-graph)")
    read_tok = os.environ.get("AIRA_OPS_READ_TOKEN") or os.environ.get("AIRA_OPS_TOKEN", "")
    app = build(agents.SdkRunner(pipeline.OPS_URL, read_tok), pipeline.OPS_URL, os.environ.get("AIRA_OPS_APPLY_TOKEN", ""))
    if a.print_graph:
        print(app.get_graph().draw_mermaid()); return
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex[:8]}}
    out = app.invoke({"account_id": a.account, "question": a.question}, cfg)
    if "__interrupt__" in out:
        pause = out["__interrupt__"][0].value
        print(json.dumps(pause, indent=2))
        d = input("\napprove / reject? ").strip()
        answer = {"decision": d, "by": input("your name: "), "reason": input("reason: "), "override": d == "approve" and pause["verdict"]["verdict"] == "block"}
        out = app.invoke(Command(resume=answer), cfg)
    print(json.dumps({k: out.get(k) for k in ("outcome", "op_id", "cost_usd")}, indent=2))

if __name__ == "__main__":
    main()
