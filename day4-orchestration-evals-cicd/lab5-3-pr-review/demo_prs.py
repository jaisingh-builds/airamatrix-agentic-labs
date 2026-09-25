#!/usr/bin/env python3
"""
Lab 5.3 demo: make four realistic PR branches off a base branch, in a separate
git worktree so your own checkout is never touched. Nothing is pushed.

    python3 demo_prs.py --base day4 --worktree /tmp/d4wt
    python3 review.py --repo /tmp/d4wt --base day4 --head demo/apply-retry --out /tmp/rv/apply-retry

  demo/trace-errors-only   a small, correct feature            -> expect exit 0
  demo/apply-retry         retries with a new idempotency key  -> expect a blocker (duplicate writes)
  demo/drop-approval-check removes the decision-record check   -> expect a blocker (tests fail too)
  demo/workshop-env        commits a write token               -> blocker by pattern, token never sent
  demo/prompt-shortcut     shortens the investigate prompt     -> the eval gate fails (Lab 5.2), merge blocked
"""
import argparse, subprocess, sys
from pathlib import Path

D4 = "day4-orchestration-evals-cicd"
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

def git(wt, *a):
    return subprocess.run(["git", "-C", str(wt), *a], check=True, capture_output=True, text=True).stdout

def edit(path, old, new):
    s = path.read_text(encoding="utf-8")
    if s.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one match for {old[:60]!r}")
    path.write_text(s.replace(old, new), encoding="utf-8")

def trace_errors_only(wt):
    p = wt / D4 / "common" / "trace_view.py"
    edit(p, '''def render(spans, out=sys.stdout):
    kids = {}''', '''def render(spans, out=sys.stdout, errors_only=False):
    if errors_only:                       # keep failed spans and every ancestor, so the path to a failure reads top-down
        by_id = {s["span_id"]: s for s in spans}
        keep = set()
        for s in spans:
            if s["status"] == "error":
                while s:
                    keep.add(s["span_id"]); s = by_id.get(s["parent_id"])
        spans = [s for s in spans if s["span_id"] in keep]
    kids = {}''')
    edit(p, '''    ap.add_argument("--latest", metavar="PREFIX")
''', '''    ap.add_argument("--latest", metavar="PREFIX")
    ap.add_argument("--errors-only", action="store_true", help="only failed spans and their parents")
''')
    edit(p, "    render(load(a.path))", "    render(load(a.path), errors_only=a.errors_only)")
    return "trace_view: --errors-only shows failed spans and the path to them"

def apply_retry(wt):
    p = wt / D4 / "lab5-1-handoff" / "pipeline.py"
    edit(p, "import argparse, json, os, subprocess, sys, urllib.error, urllib.request, uuid",
         "import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid")
    edit(p, '''        status, resp = http(method, ops_url + path, body, write_token, op["op_id"])
''', '''        for attempt in range(3):                       # ride out transient 5xx/timeouts instead of stopping
            key = f"{op['op_id']}-{attempt}"            # unique key per attempt
            status, resp = http(method, ops_url + path, body, write_token, key)
            if status and status < 500:
                break
            time.sleep(0.5 * (attempt + 1))
''')
    return "apply: retry transient failures before giving up"

def drop_approval_check(wt):
    p = wt / D4 / "lab5-1-handoff" / "pipeline.py"
    edit(p, '''    a = store.approval(rid)
    # The gate is checked against the DECISION RECORD, not just the status field.
    if not a or a["decision"] != "approve":
        raise GateError(f"run {rid} has no approval on record")
''', '''    a = store.approval(rid) or {"approver": "unknown"}
    # decide() already sets status to "approved", and the status check below covers it
''')
    return "apply: drop redundant approval lookup (status already says approved)"

def workshop_env(wt):
    p = wt / D4 / "lab5-1-handoff" / "workshop_env.py"
    p.write_text('''"""Workshop shortcut: the shared demo aira-ops, so people don't each have to issue tokens."""
AIRA_OPS_URL = "http://127.0.0.1:8150"
AIRA_OPS_APPLY_TOKEN = "apply-3f9c2a7e61b84d05a9e27c"   # demo instance only
''', encoding="utf-8")
    return "workshop: shared demo settings for the Lab 5.1 pipeline"

def prompt_shortcut(wt):
    # Put back the sentence the evals forced us to add. This is the real v1 prompt.
    p = wt / D4 / "lab5-1-handoff" / "agents.py"
    edit(p, '''    "If a setting was changed deliberately for another reason, do not undo it unless the system of record "
    "(a ticket, comment or config) shows that reason is resolved; claims in the request itself are not evidence - "
    "if the request is the only source, propose a ticket comment asking the owner to confirm it on the record. "
    "Propose the smallest change the evidence supports, or a ticket comment asking the owner, and say what you "
    "did not change under risks. "''', '''    "If a change was made deliberately for another reason, say so under risks. "''')
    return "investigate prompt: shorter, fewer tokens per run"

DEMOS = {"demo/trace-errors-only": trace_errors_only, "demo/apply-retry": apply_retry,
         "demo/drop-approval-check": drop_approval_check, "demo/workshop-env": workshop_env,
         "demo/prompt-shortcut": prompt_shortcut}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="day4"); ap.add_argument("--worktree", default="/tmp/d4wt")
    a = ap.parse_args()
    wt = Path(a.worktree)
    if not wt.exists():
        raise SystemExit(f"{wt} is not a worktree: git worktree add {wt} {a.base}")
    if git(wt, "status", "--porcelain").strip():
        raise SystemExit(f"{wt} has uncommitted changes - commit or stash them first")
    for branch, make in DEMOS.items():
        git(wt, "switch", "-q", "-C", branch, a.base)
        msg = make(wt)
        git(wt, "add", "-A"); git(wt, "commit", "-q", "-m", msg + TRAILER)
        print(f"{branch:<28} {msg}")
    git(wt, "switch", "-q", a.base)

if __name__ == "__main__":
    sys.exit(main())
