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
from test_pipeline import FakeRunner, GOOD, APPROVE, BLOCK

@unittest.skipIf(graph_langgraph is None, "langgraph not installed")
class GraphTests(unittest.TestCase):
    def cfg(self, t):
        return {"configurable": {"thread_id": t}}

    def test_graph_stops_at_the_gate_and_resumes_on_a_human_answer(self):
        calls = []
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[APPROVE]), "http://127.0.0.1:1", "x")
        out = app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t1"))
        self.assertIn("__interrupt__", out)
        self.assertNotIn("outcome", out)                  # nothing applied before a human answered
        self.assertEqual(app.get_state(self.cfg("t1")).next, ("gate",))
        out = app.invoke(Command(resume={"decision": "reject", "by": "Jai", "reason": "later"}), self.cfg("t1"))
        self.assertEqual(out["decision"]["decision"], "reject"); self.assertNotIn("outcome", out)

    def test_graph_gate_needs_a_reason_and_honours_a_block(self):
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD, GOOD], review=[BLOCK, BLOCK]), "http://127.0.0.1:1", "x")
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t2"))
        with self.assertRaises(Exception):
            app.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), self.cfg("t2"))
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t3"))
        with self.assertRaises(Exception):
            app.invoke(Command(resume={"decision": "approve", "by": "", "reason": "ok", "override": True}), self.cfg("t3"))

    def test_the_operation_id_is_checkpointed_before_apply_runs(self):
        app = graph_langgraph.build(FakeRunner(investigate=[GOOD], review=[APPROVE]), "http://127.0.0.1:1", "x")
        app.invoke({"account_id": "ACC-1001", "question": "q"}, self.cfg("t4"))
        out = app.invoke(Command(resume={"decision": "approve", "by": "Jai", "reason": "ok"}), self.cfg("t4"))
        self.assertEqual(out["outcome"], "outcome_unknown")          # nothing listening on :1
        before_apply = [h for h in app.get_state_history(self.cfg("t4")) if h.next == ("apply",)]
        self.assertEqual(before_apply[0].values["op_id"], out["op_id"],
                         "the id apply used must already be in the checkpoint taken before apply ran")

    def test_the_graph_is_the_documented_shape(self):
        app = graph_langgraph.build(FakeRunner(), "http://x", "x")
        edges = {(e.source, e.target) for e in app.get_graph().edges}
        self.assertTrue({("__start__", "investigate"), ("review", "gate"), ("apply", "__end__")} <= edges)

if __name__ == "__main__":
    unittest.main()
