#!/usr/bin/env python3
"""
The same pipeline, as a LangGraph graph (pip install langgraph langgraph-checkpoint-sqlite).

    python3 graph_langgraph.py --print-graph
    python3 graph_langgraph.py --account ACC-1001 --question "..." --db graph.sqlite     # stops at the gate
    python3 graph_langgraph.py --db graph.sqlite --thread T --decide approve --by Jai --reason "..." \
        --apply-token-file ~/.config/aira-ops/apply-token                            # later, any process

What the graph buys you, compared with pipeline.py:
  * the flow is DATA: nodes, edges and a conditional route you can print and test
  * a checkpoint after every node, keyed by thread_id
  * interrupt(): the graph pauses at the gate, and resumes with the human's answer

Which checkpointer matters:
  * InMemorySaver (the default here, and in the tests): pause and resume in ONE process.
    Exit the process and the checkpoint - including the operation id - is gone.
  * SqliteSaver (--db): the checkpoint is on disk. A new process with the same thread_id
    resumes at the gate, and a crashed apply is re-run with the SAME operation id.

The write token is read from a file only inside apply(), never from the environment: agents
run in this process, and the SDK hands the whole environment to their subprocess.
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

def build(runner, ops_url, write_token_loader, checkpointer=None):
    """write_token_loader: a callable, called only inside apply() - so the token never has to be in
    the environment of the process that runs the agents."""
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
        if s["verdict"]["verdict"] != "approve" and answer["decision"] == "approve" and not answer.get("override"):
            raise pipeline.GateError(f"the reviewer said {s['verdict']['verdict']}; approving it needs override")
        # LangGraph checkpoints BETWEEN nodes, not inside them. An op id minted inside apply()
        # would be lost if apply crashed after sending; minted here, it is saved before apply runs.
        return {"decision": answer, "op_id": s.get("op_id") or str(uuid.uuid4())}

    def apply(s: State):
        op_id = s["op_id"]                                 # from the checkpoint - same id on every retry
        method, path, body = pipeline.request_for(check_change(dict(s["proposal"]["proposed_change"])))
        status, resp = pipeline.http(method, ops_url + path, body, write_token_loader(), op_id)
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
    return g.compile(checkpointer=checkpointer or InMemorySaver())

def sqlite_checkpointer(path):
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver     # pip install langgraph-checkpoint-sqlite
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))

def token_from_file(path):
    def load():
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    return load

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account"); ap.add_argument("--question")
    ap.add_argument("--db", help="durable checkpoints (SqliteSaver); without it, one process only")
    ap.add_argument("--thread", help="resume this thread instead of starting one")
    ap.add_argument("--decide", choices=["approve", "reject"]); ap.add_argument("--by"); ap.add_argument("--reason")
    ap.add_argument("--override", action="store_true")
    ap.add_argument("--apply-token-file", default=os.environ.get("AIRA_OPS_APPLY_TOKEN_FILE", "~/.config/aira-ops/apply-token"))
    ap.add_argument("--print-graph", action="store_true")
    a = ap.parse_args()
    if not a.print_graph and not a.thread and not (a.account and a.question):
        ap.error("--account and --question (a new run), --thread (resume one), or --print-graph")
    read_tok = os.environ.get("AIRA_OPS_READ_TOKEN", "")
    agents.scrub_agent_environment()            # agents run in this process: no other secrets in it
    app = build(agents.SdkRunner(pipeline.OPS_URL, read_tok), pipeline.OPS_URL, token_from_file(a.apply_token_file),
                sqlite_checkpointer(a.db) if a.db else None)
    if a.print_graph:
        print(app.get_graph().draw_mermaid()); return
    thread = a.thread or uuid.uuid4().hex[:8]
    cfg = {"configurable": {"thread_id": thread}}
    if not a.thread:
        out = app.invoke({"account_id": a.account, "question": a.question}, cfg)
    elif a.decide:
        out = app.invoke(Command(resume={"decision": a.decide, "by": a.by or "", "reason": a.reason or "",
                                         "override": a.override}), cfg)
    else:
        out = app.invoke(None, cfg)             # re-run whatever node was in flight (e.g. a crashed apply)
    if "__interrupt__" in out:
        print(json.dumps(out["__interrupt__"][0].value, indent=2))
        print(f"\nwaiting at the gate. thread={thread}" + ("" if a.db else "  (in memory: decide in this process only)"))
        if not a.db:
            d = input("approve / reject? ").strip()
            ov = d == "approve" and input("override the reviewer? (y/N) ").strip().lower() == "y"
            out = app.invoke(Command(resume={"decision": d, "by": input("your name: "), "reason": input("reason: "),
                                             "override": ov}), cfg)
    print(json.dumps({k: out.get(k) for k in ("outcome", "op_id", "cost_usd")} | {"thread": thread}, indent=2))

if __name__ == "__main__":
    main()
