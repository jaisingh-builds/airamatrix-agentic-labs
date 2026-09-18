#!/usr/bin/env python3
"""Lab 1.1 loop contract — offline, no model, no spend.

These fail until TODO 3 is finished, which is the point: they are the
specification for "the agent finished" versus "the agent stopped".

    python3 test_stop_reasons.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "labkit" / "python"))

import agent as lab_agent  # noqa: E402


class ScriptedClient:
    """Stands in for GatewayClient. Returns canned replies in order."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = 0

    def messages(self, *args, **kwargs):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def reply(stop_reason, text="", tool=None):
    content = []
    if tool:
        content.append({"type": "tool_use", "id": "t1", "name": tool[0], "input": tool[1]})
    if text:
        content.append({"type": "text", "text": text})
    return {"stop_reason": stop_reason, "content": content,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


class StopReasonContract(unittest.TestCase):

    def run_with(self, replies, **kwargs):
        original = lab_agent.GatewayClient
        lab_agent.GatewayClient = lambda *a, **k: ScriptedClient(replies)
        try:
            return lab_agent.run_agent("goal", verbose=False, **kwargs)
        finally:
            lab_agent.GatewayClient = original

    def test_end_turn_returns_the_answer(self):
        self.assertIn("62.4", self.run_with([reply("end_turn", "The overage is 62.4%.")]))

    def test_end_turn_with_no_text_is_not_an_answer(self):
        with self.assertRaises(Exception):
            self.run_with([reply("end_turn", "")])

    def test_max_tokens_is_not_an_answer(self):
        """A truncated reply must not be returned as if it were the result."""
        with self.assertRaises(lab_agent.Truncated):
            self.run_with([reply("max_tokens", "The overage is 6")])

    def test_refusal_is_not_an_answer(self):
        with self.assertRaises(lab_agent.Refused):
            self.run_with([reply("refusal")])

    def test_unknown_stop_reason_fails_safely(self):
        """A value this code has never seen is not success."""
        with self.assertRaises(lab_agent.UnhandledStop):
            self.run_with([reply("some_stop_reason_from_2027")])

    def test_tool_use_then_end_turn_completes(self):
        answer = self.run_with([
            reply("tool_use", tool=("read_file", {"path": "limits.txt"})),
            reply("end_turn", "max_queue_depth is 500."),
        ])
        self.assertIn("500", answer)

    def test_step_limit_halts(self):
        with self.assertRaises(lab_agent.StepLimitExceeded):
            self.run_with([reply("tool_use", tool=("read_file", {"path": "limits.txt"}))],
                          max_steps=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
