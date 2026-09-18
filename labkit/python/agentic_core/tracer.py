"""Run tracing. Every step an agent takes lands here as one JSON line.

Day 1 uses this to see what the agent did. Day 4 uses the same files to debug a
failed run from its trace alone — so the observability lab is instrumented by
something participants have used since their first exercise.
"""
import json
import time
import uuid
from pathlib import Path


class Tracer:
    def __init__(self, name: str, root: str | None = None) -> None:
        self.run_id = f"{name}-{uuid.uuid4().hex[:8]}"
        base = Path(root) if root else Path(__file__).resolve().parents[3] / "traces"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{self.run_id}.jsonl"
        self._t0 = time.time()

    def emit(self, kind: str, **fields) -> None:
        record = {"run_id": self.run_id, "t": round(time.time() - self._t0, 3),
                  "kind": kind, **fields}
        with self.path.open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def step(self, n: int, stop_reason: str, text: str = "", tools: list | None = None) -> None:
        self.emit("step", n=n, stop_reason=stop_reason,
                  text=(text or "")[:400], tools=tools or [])

    def tool(self, name: str, args: dict, result: str, ok: bool = True) -> None:
        self.emit("tool_result", name=name, args=args, ok=ok, result=str(result)[:400])
