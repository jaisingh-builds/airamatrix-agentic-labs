"""The assembler, replayed against a stream captured from the real gateway."""
import json, unittest
from pathlib import Path
from stream import assemble, StreamCancelled

FIX = Path(__file__).parent / "fixtures" / "gateway_stream_tool_use.sse"

class Assemble(unittest.TestCase):
    def test_real_stream_text_arrives_in_pieces_and_joins(self):
        pieces = []
        msg = assemble(FIX.read_text().splitlines(), on_text=pieces.append)
        self.assertGreater(len(pieces), 1, "text should arrive as more than one delta")
        self.assertEqual("".join(pieces), msg["content"][0]["text"])

    def test_real_stream_tool_call_is_parsed_only_when_complete(self):
        msg = assemble(FIX.read_text().splitlines())
        tool = [b for b in msg["content"] if b["type"] == "tool_use"][0]
        self.assertEqual((tool["name"], tool["input"]), ("get_ticket", {"id": "T-1001"}))
        self.assertTrue(tool["id"].startswith("toolu_"))
        self.assertEqual(msg["stop_reason"], "tool_use")
        self.assertGreater(msg["usage"]["output_tokens"], 0)

    def test_thinking_block_keeps_its_signature(self):
        # Shape per the documented thinking_delta / signature_delta events.
        ev = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "check the "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "config"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-abc"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
        ]
        msg = assemble(["data: " + json.dumps(e) for e in ev])
        self.assertEqual(msg["content"][0], {"type": "thinking", "thinking": "check the config", "signature": "sig-abc"})

    def test_cancellation_is_checked_while_reading(self):
        lines = FIX.read_text().splitlines()
        seen = {"n": 0}
        def cancelled():
            seen["n"] += 1
            return seen["n"] > 5
        with self.assertRaises(StreamCancelled):
            assemble(lines, cancelled=cancelled)

    def test_gateway_error_event_raises(self):
        with self.assertRaises(RuntimeError):
            assemble(['data: {"type":"error","error":{"type":"overloaded_error"}}'])

if __name__ == "__main__":
    unittest.main()
