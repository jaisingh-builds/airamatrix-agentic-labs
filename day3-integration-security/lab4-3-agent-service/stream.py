"""
Streaming Messages client: turns the gateway's server-sent events back into
one assistant message, while handing text to the caller as it arrives.

Standard library only. The event shapes below were captured from the training
gateway on 2026-09-25 (fixtures/gateway_stream_tool_use.sse):

  message_start                  -> input usage
  content_block_start  (index)   -> a new block: text | tool_use | thinking | redacted_thinking
  content_block_delta  (index)   -> text_delta | input_json_delta | thinking_delta | signature_delta
  content_block_stop   (index)   -> a tool_use's JSON is only complete HERE
  message_delta                  -> stop_reason, output usage
  message_stop

Three things that break naive clients, all handled here:
  * a tool call's arguments arrive as JSON *fragments* (the first is often empty);
  * thinking blocks must be sent back unmodified, signature included, or the next
    turn is rejected;
  * cancellation and timeouts have to be checked while reading, not after.
"""
import json, socket, urllib.error, urllib.request

class StreamCancelled(Exception):
    pass

def assemble(lines, on_text=None, cancelled=lambda: False):
    """Consume SSE lines (str) and return {content, stop_reason, usage}."""
    blocks, partial_json = {}, {}
    usage, stop_reason = {}, None
    for raw in lines:
        if cancelled():
            raise StreamCancelled()
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        e = json.loads(line[5:].strip())
        t = e.get("type")
        if t == "message_start":
            usage.update(e["message"].get("usage", {}))
        elif t == "content_block_start":
            b = dict(e["content_block"])
            if b.get("type") == "tool_use":
                b["input"] = {}
                partial_json[e["index"]] = ""
            blocks[e["index"]] = b
        elif t == "content_block_delta":
            i, d = e["index"], e["delta"]
            dt = d.get("type")
            if dt == "text_delta":
                blocks[i]["text"] = blocks[i].get("text", "") + d["text"]
                if on_text:
                    on_text(d["text"])
            elif dt == "input_json_delta":
                partial_json[i] += d.get("partial_json", "")
            elif dt == "thinking_delta":
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + d["thinking"]
            elif dt == "signature_delta":
                blocks[i]["signature"] = blocks[i].get("signature", "") + d["signature"]
        elif t == "content_block_stop":
            i = e["index"]
            if i in partial_json:
                blocks[i]["input"] = json.loads(partial_json.pop(i) or "{}")
        elif t == "message_delta":
            stop_reason = e["delta"].get("stop_reason", stop_reason)
            usage.update({k: v for k, v in e.get("usage", {}).items() if k == "output_tokens"})
        elif t == "error":
            raise RuntimeError(f"gateway stream error: {e.get('error')}")
    content = [blocks[i] for i in sorted(blocks)]
    return {"content": content, "stop_reason": stop_reason, "usage": usage}

def abort(resp):
    """Close a live streaming response from ANOTHER thread.

    The reading thread may be blocked inside a socket read, waiting for bytes
    that are not coming. Checking a flag between chunks can't reach it there;
    shutting the socket down can - the blocked read returns at once. Closing
    the connection is also how the gateway learns to stop generating (and
    billing) the rest of the answer.
    """
    try:
        resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass

def stream_messages(base_url, api_key, payload, on_text=None, cancelled=lambda: False,
                    read_timeout=60, on_open=None):
    """POST a streaming request and assemble the reply. Raises on HTTP errors.

    on_open(resp) is called with the live response, so a canceller can abort() it."""
    body = dict(payload, stream=True)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/messages", data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "authorization": f"Bearer {api_key}"})
    # read_timeout bounds the gap BETWEEN chunks, so a stalled stream fails
    # instead of hanging the request thread forever.
    with urllib.request.urlopen(req, timeout=read_timeout) as resp:
        if on_open:
            on_open(resp)
        lines = (l.decode("utf-8", "replace") for l in resp)
        try:
            reply = assemble(lines, on_text=on_text, cancelled=cancelled)
        except (OSError, ValueError):
            if cancelled():            # the read failed because we aborted it on purpose
                raise StreamCancelled()
            raise
        if cancelled():                # an aborted socket can also look like a clean EOF
            raise StreamCancelled()
        return reply
