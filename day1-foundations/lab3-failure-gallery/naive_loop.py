"""A working agent loop with five real defects.

Every one of these has been seen in production. Your job is to find and fix them
using the failing tests as your specification:

    python3 test_gallery.py

Do not change the tests or the fake gateway. Fix this file.
"""


class StepLimitExceeded(RuntimeError):
    pass


class RepeatedCallDetected(RuntimeError):
    pass


def run(gateway, tools, goal, max_steps=8):
    """Run an agent to completion and return (answer, transcript).

    transcript is the list of tool outcomes: (name, args, result, ok)
    """
    messages = [{"role": "user", "content": goal}]
    transcript = []

    while True:                                   # DEFECT 1
        response = gateway.messages(messages, tools=tools)
        blocks = response.get("content", [])
        stop = response.get("stop_reason")

        if stop != "tool_use":
            answer = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return answer, transcript

        # DEFECT 2
        for block in blocks:                      # DEFECT 5
            if block.get("type") != "tool_use":
                continue
            name, args = block["name"], block.get("input", {})
            result, ok = tools.dispatch(name, args)
            transcript.append((name, args, result, ok))
            messages.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": result,
                # DEFECT 3
            }]})
        # DEFECT 2 and DEFECT 4 are also visible from here.
