#!/usr/bin/env python3
"""
Lab 4.1 — Tool design clinic, measured.

Runs the same ten goals against the real model with one toolset, executes every
tool call for real against a throwaway aira-ops, and scores the result.

    python3 clinic.py --tools bad
    python3 clinic.py --tools good
    python3 clinic.py --tools mine        # your rewrite, in mine.py
    python3 clinic.py --tools bad good    # side by side

Costs roughly $0.05-0.15 per toolset on claude-sonnet. Needs .env (gateway key).
The aira-ops it starts is private to this run: own port, own token, own database.
"""
import argparse, json, os, re, secrets, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "labkit" / "python"))
from agentic_core import Config, GatewayClient, BudgetGuard, BudgetExceeded  # noqa: E402
import toolsets  # noqa: E402

SYSTEM = ("You answer questions about AiraMatrix's support and operations system using the tools "
          "provided. Be concise: answer in one or two sentences with the specific ids and values. "
          "If you cannot find the answer, say so plainly rather than guessing.")
MAX_STEPS = 6

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class ThrowawayOps:
    """A private aira-ops for this run, so measurement never touches a shared one."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port, self.token = free_port(), secrets.token_hex(16)
        self.p = subprocess.Popen([sys.executable, str(REPO / "day3-integration-security/aira-ops/aira_ops.py"),
                                   "--port", str(self.port), "--db", str(Path(self.tmp.name) / "c.sqlite"), "--quiet"],
                                  env=dict(os.environ, AIRA_OPS_TOKEN=self.token),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=0.2); break
            except Exception:
                time.sleep(0.1)
        return self

    def __exit__(self, *a):
        self.p.terminate(); self.p.wait(); self.tmp.cleanup()

    def api(self, method, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "X-Actor": "clinic"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

def load_toolset(name):
    if name == "mine":
        import mine
        return mine.TOOLS, mine.run
    return toolsets.TOOLSETS[name]

# An answer that says it could not find the answer is never a pass, whatever else
# it happens to contain. (Found in the first real run: "I wasn't able to find a
# config key" passed because the pattern "on" matched inside "config".)
GAVE_UP = re.compile(r"(wasn.t|was not|not) able to|couldn.t|could not|cannot find|can.t find|unable to", re.I)

def correct(answer, expect):
    if GAVE_UP.search(answer):
        return False
    return all(re.search(e, answer, re.I) for e in expect)

def run_goal(client, budget, ops, tools, runner, goal):
    messages = [{"role": "user", "content": goal}]
    calls = errors = 0
    for step in range(1, MAX_STEPS + 1):
        budget.check()
        r = client.messages(messages, tools=tools, system=SYSTEM, max_tokens=800)
        budget.record(r.get("usage", {}))
        messages.append({"role": "assistant", "content": r["content"]})
        if r.get("stop_reason") != "tool_use":
            text = " ".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
            return text.strip(), calls, errors, step
        results = []
        for b in r["content"]:
            if b.get("type") != "tool_use":
                continue
            calls += 1
            try:
                out, is_err = runner(ops.api, b["name"], b.get("input") or {})
            except Exception as e:  # a tool failure is data for the model, never a crashed run
                out, is_err = f"tool raised {type(e).__name__}: {e}", True
            errors += int(is_err)
            results.append({"type": "tool_result", "tool_use_id": b["id"], "content": out[:6000], "is_error": is_err})
        messages.append({"role": "user", "content": results})
    return "(step limit reached)", calls, errors, MAX_STEPS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", nargs="+", default=["bad"], choices=["bad", "good", "mine"])
    ap.add_argument("--goals", default=str(HERE / "goals.json"))
    a = ap.parse_args()
    cfg = Config()
    client = GatewayClient(cfg)
    goals = json.loads(Path(a.goals).read_text())
    board = {}
    out_dir = HERE / "results"; out_dir.mkdir(exist_ok=True)
    with ThrowawayOps() as ops:
        for name in a.tools:
            tools, runner = load_toolset(name)
            budget = BudgetGuard(max(cfg.budget_usd, 0.50), cfg.model)
            rows = []
            print(f"\n=== toolset: {name}  ({', '.join(t['name'] for t in tools)})")
            for g in goals:
                try:
                    ans, calls, errs, steps = run_goal(client, budget, ops, tools, runner, g["goal"])
                except BudgetExceeded as e:
                    print("  budget:", e); break
                ok = correct(ans, g["expect"])
                rows.append({"id": g["id"], "ok": ok, "calls": calls, "errors": errs, "steps": steps, "answer": ans})
                print(f"  {'PASS' if ok else 'FAIL'}  #{g['id']:<2} calls={calls} errors={errs}  {ans[:90]!r}")
            n = len(rows) or 1
            board[name] = {"correct": sum(r["ok"] for r in rows), "goals": len(rows),
                           "calls": round(sum(r["calls"] for r in rows) / n, 1),
                           "errors": sum(r["errors"] for r in rows),
                           "cost": round(budget.spent, 4)}
            (out_dir / f"{name}.json").write_text(json.dumps({"summary": board[name], "rows": rows}, indent=2))
    print("\n  toolset   correct   avg calls/goal   tool errors   cost")
    for name, b in board.items():
        print(f"  {name:<8}  {b['correct']:>2}/{b['goals']:<5}   {b['calls']:>6}           {b['errors']:>5}       ${b['cost']:.3f}")
    print(f"\n  model: {cfg.model}  ·  every tool call executed against a private aira-ops  ·  results/ has the detail")

if __name__ == "__main__":
    main()
