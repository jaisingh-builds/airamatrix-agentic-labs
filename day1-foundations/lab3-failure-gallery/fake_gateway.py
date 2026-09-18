"""A model that does exactly what the script says.

Every failure in this lab is reproducible on every machine, in the same way,
for free. Real models fail intermittently; you cannot teach from that.
"""


def text(body: str) -> dict:
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text", "text": body}]}


def tool_use(*calls: tuple) -> dict:
    """tool_use(("read_file", {"path": "a.txt"}), ...) -> one assistant turn."""
    blocks = [{"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": args}
              for i, (name, args) in enumerate(calls)]
    return {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 20},
            "content": blocks}


def tool_use_varying(name: str) -> dict:
    """A tool_use whose arguments differ on every call.

    An agent can loop forever without ever repeating itself - so this isolates
    "no step limit" from "repeated identical call". They are different bugs and
    need different fixes.
    """
    marker = tool_use((name, {}))
    marker["_vary"] = True
    marker["_tool"] = name
    return marker


class FakeGateway:
    """Replays a script. The last entry repeats forever, which is how we
    reproduce an agent that will not stop."""

    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls = 0
        self.seen_messages: list[list] = []

    def messages(self, messages, tools=None, system=None, max_tokens=1024, **kwargs) -> dict:
        self.seen_messages.append(list(messages))
        response = self.script[min(self.calls, len(self.script) - 1)]
        if response.get("_vary"):
            response = tool_use((response["_tool"], {"path": f"page-{self.calls}.txt"}))
        self.calls += 1
        if self.calls > 200:
            raise RuntimeError("fake gateway called 200 times - the agent is not terminating")
        return response
