"""Offline tests for the judge plumbing - a fake client, no model.

    python3 -m unittest test_judge -v
"""
import json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import judge  # noqa: E402

class FakeClient:
    class cfg: model = "claude-sonnet"
    def __init__(self, replies): self.replies, self.prompts = list(replies), []
    def messages(self, messages, system=None, max_tokens=0, model=None, **_):
        self.prompts.append(messages[0]["content"])
        return {"content": [{"type": "text", "text": self.replies.pop(0)}],
                "usage": {"input_tokens": 1000, "output_tokens": 50}}

class JudgeTests(unittest.TestCase):
    def test_parses_a_verdict_wrapped_in_prose(self):
        v = judge.parse_verdict('Sure. {"verdict": "fail", "score": 2, "reason": "reverts a mitigation"} Done.')
        self.assertEqual((v["verdict"], v["score"]), ("fail", 2))

    def test_an_unparseable_reply_is_an_error_not_a_pass(self):
        for bad in ("Looks good to me!", '{"verdict": "approve"}', ""):
            with self.assertRaises(ValueError):
                judge.parse_verdict(bad)

    def test_a_reply_with_only_thinking_is_an_error(self):
        class Thinker(FakeClient):
            def messages(self, *a, **k):
                return {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": "..."}], "usage": {}}
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            judge.judge(Thinker([]), "q", {})

    def test_reference_is_only_sent_in_reference_mode(self):
        p = judge.judge_prompt("q", {"a": 1})
        self.assertNotIn("<reference>", p)
        self.assertIn("<reference>\nfacts\n</reference>", judge.judge_prompt("q", {"a": 1}, "facts"))
        self.assertIn("<proposal>", p)                      # untrusted content is fenced

    def test_agreement_separates_false_passes_from_false_fails(self):
        rows = [{"id": "a", "human": "fail", "judge": "pass"}, {"id": "b", "human": "pass", "judge": "fail"},
                {"id": "c", "human": "pass", "judge": "pass"}, {"id": "d", "human": "fail", "judge": None}]
        a = judge.agreement(rows)
        self.assertEqual((a["false_pass"], a["false_fail"], a["errors"], a["agreement"]), (["a"], ["b"], ["d"], 0.333))

    def test_calibrate_uses_the_item_question_and_reference_overrides(self):
        cases = {"k": {"question": "case q", "reference": "case facts"}}
        items = [{"id": "x", "case": "k", "human": "pass", "proposal": {}},
                 {"id": "y", "case": "k", "human": "fail", "proposal": {}, "question": "item q", "reference": "item facts"}]
        fc = FakeClient(['{"verdict":"pass"}', '{"verdict":"pass"}'])
        rows, cost = judge.calibrate(items, cases, fc, "reference")
        self.assertIn("case facts", fc.prompts[0]); self.assertIn("item q", fc.prompts[1]); self.assertIn("item facts", fc.prompts[1])
        self.assertEqual(judge.agreement(rows)["false_pass"], ["y"])
        self.assertAlmostEqual(cost, 2 * (1000 * 0.000002 + 50 * 0.00001))

    def test_calibration_set_is_balanced_and_sourced(self):
        items = json.loads((HERE / "judge_calibration.json").read_text())["items"]
        cases = {c["id"] for c in json.loads((HERE / "golden" / "cases.json").read_text())["cases"]}
        self.assertGreaterEqual(min(sum(i["human"] == h for i in items) for h in ("pass", "fail")), 3)
        for i in items:
            self.assertIn(i["case"], cases); self.assertTrue(i["source"]); self.assertTrue(i["why"])

if __name__ == "__main__":
    unittest.main()
