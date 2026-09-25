#!/usr/bin/env python3
"""
Print a trace as a tree - enough to debug a failed run from its trace alone.

    python3 trace_view.py ../../traces/lab5-1-<run>.jsonl
    python3 trace_view.py --latest lab5-1          # newest trace with that prefix
"""
import argparse, json, sys
from pathlib import Path

def load(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]

def render(spans, out=sys.stdout, errors_only=False):
    if errors_only:                       # keep failed spans and every ancestor, so the path to a failure reads top-down
        by_id = {s["span_id"]: s for s in spans}
        keep = set()
        for s in spans:
            if s["status"] == "error":
                while s:
                    keep.add(s["span_id"]); s = by_id.get(s["parent_id"])
        spans = [s for s in spans if s["span_id"] in keep]
    kids = {}
    for s in spans:
        kids.setdefault(s["parent_id"], []).append(s)
    for v in kids.values():
        v.sort(key=lambda s: s["start"])
    def walk(parent, depth):
        for s in kids.get(parent, []):
            mark = "x" if s["status"] == "error" else "-"
            keep = {k: v for k, v in s["attrs"].items() if k not in ("output",)}
            attrs = " ".join(f"{k}={json.dumps(v)[:70]}" for k, v in keep.items())
            print(f"{'  ' * depth}{mark} {s['name']}  {s['duration_ms']} ms  {attrs}", file=out)
            if s["error"]:
                print(f"{'  ' * depth}    ERROR {s['error']}", file=out)
            walk(s["span_id"], depth + 1)
    walk(None, 0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--latest", metavar="PREFIX")
    ap.add_argument("--errors-only", action="store_true", help="only failed spans and their parents")
    a = ap.parse_args()
    if a.latest:
        root = Path(__file__).resolve().parents[2] / "traces"
        files = sorted(root.glob(f"{a.latest}*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise SystemExit(f"no traces matching {a.latest}*.jsonl in {root}")
        a.path = files[-1]
        print(f"# {a.path.name}")
    if not a.path:
        ap.error("give a trace file or --latest PREFIX")
    render(load(a.path), errors_only=a.errors_only)

if __name__ == "__main__":
    main()
