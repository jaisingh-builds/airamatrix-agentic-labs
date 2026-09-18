#!/usr/bin/env python3
"""Five failures, five tests. Fix naive_loop.py until all five pass.

No model is called: every failure is scripted, so it reproduces identically on
every machine and costs nothing.
"""
import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fake_gateway import FakeGateway, text, tool_use, tool_use_varying   # noqa: E402
from toy_tools import ToyTools                         # noqa: E402
import naive_loop                                      # noqa: E402


class Failure1NoStepLimit(unittest.TestCase):
    """An agent that never stops. The most expensive bug in this programme:
    it does not crash, it just bills until someone notices."""

    def test_agent_stops(self):
        # never repeats itself, so ONLY a step limit can stop it
        gateway = FakeGateway([tool_use_varying("read_file")])
        with self.assertRaises(naive_loop.StepLimitExceeded,
                               msg="the loop must give up after max_steps"):
            naive_loop.run(gateway, ToyTools(), "read the limits", max_steps=5)
        self.assertLessEqual(gateway.calls, 6, "must stop at the limit, not run on")


class Failure2MissingAssistantTurn(unittest.TestCase):
    """The agent sent tool results without ever recording what the model asked
    for. The conversation no longer makes sense: the model sees answers to
    questions it has no record of asking."""

    def test_assistant_turn_precedes_results(self):
        gateway = FakeGateway([
            tool_use(("read_file", {"path": "limits.txt"}), ("save_report", {"body": "x"})),
            text("done"),
        ])
        _, transcript = naive_loop.run(gateway, ToyTools(), "read then save")
        self.assertEqual(len(transcript), 2, "both tools must run")
        final = gateway.seen_messages[-1]
        roles = [m.get("role") for m in final]
        self.assertIn("assistant", roles,
                      "the assistant's tool_use turn must be appended before the results")
        assistant_idx = roles.index("assistant")
        result_idx = next(i for i, m in enumerate(final)
                          if m.get("role") == "user" and isinstance(m.get("content"), list)
                          and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                  for b in m["content"]))
        self.assertLess(assistant_idx, result_idx,
                        "the assistant turn must come BEFORE the tool results")


class Failure3SilentFailure(unittest.TestCase):
    """A tool failed. The agent was never told, so it reported success.
    is_error is not decoration."""

    def test_error_is_flagged_to_the_model(self):
        gateway = FakeGateway([
            tool_use(("read_file", {"path": "does-not-exist.txt"})),
            text("I read the file successfully."),
        ])
        naive_loop.run(gateway, ToyTools(), "read the config")
        final = gateway.seen_messages[-1]
        results = [block for message in final if isinstance(message.get("content"), list)
                   for block in message["content"]
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
        self.assertTrue(results, "a tool_result must be sent")
        self.assertTrue(results[0].get("is_error"),
                        "a failed tool must come back with is_error=True")


class Failure4RepeatedCall(unittest.TestCase):
    """Same tool, same arguments, over and over. Each repeat costs a full
    round-trip and changes nothing."""

    def test_identical_repeat_is_caught(self):
        gateway = FakeGateway([tool_use(("read_file", {"path": "limits.txt"}))])
        with self.assertRaises((naive_loop.RepeatedCallDetected, naive_loop.StepLimitExceeded),
                               msg="detect a call repeated with identical arguments"):
            naive_loop.run(gateway, ToyTools(), "read the limits", max_steps=20)
        tools_used = gateway.calls
        self.assertLess(tools_used, 10, "should give up well before the step limit")


class Failure5ResultsSplitAcrossMessages(unittest.TestCase):
    """All tool_results from one turn belong in ONE user message. Splitting them
    works - and quietly teaches the model to stop calling tools in parallel."""

    def test_results_are_batched_into_one_message(self):
        gateway = FakeGateway([
            tool_use(("read_file", {"path": "limits.txt"}), ("save_report", {"body": "x"})),
            text("done"),
        ])
        naive_loop.run(gateway, ToyTools(), "read then save")
        final = gateway.seen_messages[-1]
        user_result_messages = [
            message for message in final
            if message.get("role") == "user" and isinstance(message.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in message["content"])
        ]
        self.assertEqual(len(user_result_messages), 1,
                         "two tool_results were sent as two messages; they must be one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
