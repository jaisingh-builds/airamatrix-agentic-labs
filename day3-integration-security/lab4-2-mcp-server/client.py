#!/usr/bin/env python3
"""
A second MCP client, in a second language, with no SDK at all.

The TypeScript server was written once. Claude Code uses it; so does this 60-line
Python script. That is the N x M problem going away: the protocol is the contract.

    python3 client.py                 # list what the server offers
    python3 client.py get_ticket '{"id":"T-1001"}'

MCP over stdio is newline-delimited JSON-RPC 2.0. Three messages to start:
initialize -> (response) -> notifications/initialized. Then any request.
"""
import json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

class McpClient:
    def __init__(self, cmd=None, env=None):
        cmd = cmd or ["node", "--experimental-strip-types", "--no-warnings", str(HERE / "server.ts")]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env or os.environ.copy())
        self._id = 0
        self.info = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "aira-python-client", "version": "1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()

    def request(self, method, params=None):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server exited: " + self.p.stderr.read()[-500:])
            msg = json.loads(line)
            if msg.get("id") == self._id:          # skip notifications / other ids
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    def call(self, tool, args):
        return self.request("tools/call", {"name": tool, "arguments": args})

    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=5)
        self.p.stdout.close(); self.p.stderr.close()

def main():
    c = McpClient()
    try:
        if len(sys.argv) > 1:
            args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
            r = c.call(sys.argv[1], args)
            print(("ERROR " if r.get("isError") else "") + r["content"][0]["text"])
            return
        s = c.info["serverInfo"]
        print(f"connected to {s['name']} {s['version']} over stdio (protocol {c.info['protocolVersion']})\n")
        for t in c.request("tools/list")["tools"]:
            a = t.get("annotations", {})
            kind = "read " if a.get("readOnlyHint") else ("WRITE*" if a.get("destructiveHint") else "write")
            print(f"  tool      {kind}  {t['name']:22} {t.get('title','')}")
        for r in c.request("resources/list")["resources"]:
            print(f"  resource         {r['uri']:22} {r.get('title','')}")
        for r in c.request("resources/templates/list")["resourceTemplates"]:
            print(f"  template         {r['uriTemplate']:22} {r.get('title','')}")
        for p in c.request("prompts/list")["prompts"]:
            print(f"  prompt           {p['name']:22} {p.get('title','')}")
        print("\n  * destructive: a client should always ask a human first")
    finally:
        c.close()

if __name__ == "__main__":
    main()
