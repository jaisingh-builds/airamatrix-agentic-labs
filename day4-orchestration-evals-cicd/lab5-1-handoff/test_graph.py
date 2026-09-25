"""The LangGraph version pauses at the gate and resumes with a human answer.
Skipped unless langgraph is installed (pip install langgraph)."""
import sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from langgraph.types import Command
    import graph_langgraph
except ImportError:
    graph_langgraph = None
from test_pipeline import FakeRunner, GOOD, APPROVE, BLOCK, REVISE

@unittest.skipIf(graph_langgraph is None, "langgraph not installed")
class GraphTests(unittest.TestCase):
    def cfg(self, t):
        return {"configurable": {"thread_id": t}}

    def test_graph_stops_at_the_gate_and_resumes_on_a_human_answer(self):
        calls = []
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[APPROVE]), "http://127.0.0.1:1", lambda: "x")
        out = app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t1"))
        self.assertIn("__interrupt__", out)
        self.assertNotIn("outcome", out)                  # nothing applied before a human answered
        self.assertEqual(app.get_state(self.cfg("t1")).next, ("gate",))
        out = app.invoke(Command(resume={"decision": "reject", "by": "Jai", "reason": "later"}), self.cfg("t1"))
        self.assertEqual(out["decision"]["decision"], "reject"); self.assertNotIn("outcome", out)

    def test_graph_gate_needs_a_reason_and_honours_a_block(self):
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD, GOOD], review=[BLOCK, BLOCK]), "http://127.0.0.1:1", lambda: "x")
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t2"))
        with self.assertRaises(Exception):
            app.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), self.cfg("t2"))
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t3"))
        with self.assertRaises(Exception):
            app.invoke(Command(resume={"decision": "approve", "by": "", "reason": "ok", "override": True}), self.cfg("t3"))

    def test_graph_gate_treats_revise_as_not_approved(self):
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[REVISE]), "http://127.0.0.1:1", lambda: "x")
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t5"))
        with self.assertRaisesRegex(Exception, "revise"):
            app.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), self.cfg("t5"))

    def test_the_operation_id_is_checkpointed_before_apply_runs(self):
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[APPROVE]), "http://127.0.0.1:1", lambda: "x")
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t4"))
        out = app.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), self.cfg("t4"))
        self.assertEqual(out["outcome"], "outcome_unknown")          # nothing listening on :1
        before_apply = [h for h in app.get_state_history(self.cfg("t4")) if h.next == ("apply",)]
        self.assertEqual(before_apply[0].values["op_id"], out["op_id"],
                         "the id apply used must already be in the checkpoint taken before apply ran")

    def test_a_new_process_resumes_at_the_gate_and_a_crashed_apply_reuses_its_operation_id(self):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401
        except ImportError:
            self.skipTest("langgraph-checkpoint-sqlite not installed")
        import tempfile, os
        db = os.path.join(tempfile.mkdtemp(), "graph.sqlite")
        cfg = self.cfg("durable")
        # process 1: runs the agents, stops at the gate, exits
        app1 = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[APPROVE]), "http://127.0.0.1:1",
                                     lambda: "x", graph_langgraph.sqlite_checkpointer(db))
        app1.invoke({"account_id": "ACC-1001", "question": "q"}, cfg)
        del app1
        # process 2: a human approves; apply crashes before it finishes
        calls = []
        def crashing_loader():
            calls.append(1)
            raise RuntimeError("process killed during apply")
        app2 = graph_langgraph.build(FakeRunner(), "http://127.0.0.1:1", crashing_loader, graph_langgraph.sqlite_checkpointer(db))
        self.assertEqual(app2.get_state(cfg).next, ("gate",), "the pause did not survive the restart")
        with self.assertRaises(RuntimeError):
            app2.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), cfg)
        op_before = app2.get_state(cfg).values["op_id"]
        del app2
        # process 3: re-runs the in-flight apply - with the SAME operation id, and no agent runs again
        app3 = graph_langgraph.build(FakeRunner(), "http://127.0.0.1:1", lambda: "x", graph_langgraph.sqlite_checkpointer(db))
        self.assertEqual(app3.get_state(cfg).next, ("apply",))
        out = app3.invoke(None, cfg)
        self.assertEqual((out["op_id"], out["outcome"]), (op_before, "outcome_unknown"))

    def test_the_graph_is_the_documented_shape(self):
        app = graph_langgraph.build(FakeRunner(), "http://x", lambda: "x")
        edges = {(e.source, e.target) for e in app.get_graph().edges}
        self.assertTrue({("__start__", "investigate"), ("review", "gate"), ("apply", "__end__")} <= edges)

if __name__ == "__main__":
    unittest.main()
