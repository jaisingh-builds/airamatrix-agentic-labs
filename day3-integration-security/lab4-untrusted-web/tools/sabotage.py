#!/usr/bin/env python3
"""S14 — remove each control, prove the suite notices.

A suite that passes is not evidence. A suite that FAILS when you delete the
control is. Any control whose removal leaves the suite green is one the lab
teaches and has never verified, and that is worse than no test at all: it buys
confidence nothing paid for.

Each mutation below deletes exactly one control, in a throwaway copy of the
lab, and the whole suite is re-run there. Exit is non-zero if any mutation
survives.

Three properties this harness must have, each bought at a cost:

  scratch copy   The tree under test is copied to /private/tmp and mutated
                 there. Patch-and-restore would leave a half-mutated lab behind
                 on any crash, ^C or failed assertion, and the failure mode is
                 silent: the next person to run the suite sees a real-looking
                 red. A fresh copy per mutation also means no mutation can
                 inherit the previous one's damage.

  count diff     Compared against the baseline test COUNT, not just pass/fail.
                 A setUpClass error takes its whole class down and still prints
                 as an ordinary `ERROR:` line; S12 lost six tests that way and
                 the run read as 33/33 green. A mutation that breaks collection
                 would otherwise score as "caught" while having disabled the
                 very tests that would have caught it.

  port guard     The fixture origins BORROW a port that is already open
                 (fixture_sites.origins.owns_servers / sink.owns_server exist to
                 say so). A scratch run that borrows is exercising the
                 UNMUTATED tree's servers, and every verdict here becomes a coin
                 flip. Refused up front, re-checked before every suite, and the
                 fixtures' own borrow warning is treated as a failed run.

Python standard library only. Suites run sequentially — concurrently is how the
borrow above happens.
"""
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
LAB = HERE.parent
REPO = LAB.parents[1]

# Under /private/tmp, never inside the tree: a scratch dir that lands in the repo
# is a modified tree, which is the one thing this harness must never leave.
SCRATCH_ROOT = "/private/tmp"

# The three origins plus the exfil sink. Any of these already open means some
# other process owns it and ours would borrow rather than bind.
FIXTURE_PORTS = (8141, 8142, 8143, 8144)

# The fixtures announce a borrow on stderr. Cheaper and more certain than any
# probe of ours, because it is the code that actually did the binding talking.
BORROW_MARKERS = (
    "fixture_sites.origins: borrowing",
    "fixture_sites.sink: port 8144 already served",
)

SUITE_TIMEOUT = 300
# How long to wait for a neighbour's lab4 run to release the fixture ports.
PORT_WAIT = 180
# A borrow that slips through the pre-flight check is retried, never accepted.
BORROW_RETRIES = 4


# --------------------------------------------------------------- mutations

# `anchor` must appear EXACTLY once. A mutation whose anchor has drifted would
# apply nothing and then report the control as "survived" — or, worse, as
# "caught" by whatever else is red that day. See apply(): it aborts the run.
MUTATIONS = [
    {
        "id": "M1",
        "name": "gate disabled — check_url returns unconditionally",
        "file": "web_tools/gate.py",
        "expect": "refusal table, injection test, sink log",
        "anchor": '    """Return the validated (scheme, host, port), or raise PolicyRefusal."""\n',
        "replace": (
            '    """Return the validated (scheme, host, port), or raise PolicyRefusal."""\n'
            "    # SABOTAGE M1: every refusal path below is now dead code. Still\n"
            "    # returns a triple so callers fail on policy, not on TypeError.\n"
            "    _sabotage = urlsplit(url if isinstance(url, str) else '')\n"
            "    return (_sabotage.scheme.lower(), _sabotage.hostname or '',\n"
            "            _sabotage.port or DEFAULT_PORTS.get(_sabotage.scheme.lower(), 80))\n"
        ),
    },
    {
        "id": "M2",
        "name": "redirect handler removed — urllib's silent default follows the hop",
        "file": "web_tools/redirects.py",
        "expect": "cross-host redirect case",
        "anchor": "        _GuardedRedirectHandler(check, max_hops),\n",
        # Dropping the handler does not disable redirects; build_opener installs
        # the stdlib HTTPRedirectHandler, which follows every Location without
        # asking. That is the real-world default this slice exists to replace.
        "replace": "        # SABOTAGE M2: handler removed; stdlib follows hops unchecked.\n",
    },
    {
        "id": "M3",
        "name": "envelope nonce replaced with a constant",
        "file": "web_tools/envelope.py",
        "expect": "envelope-escape case",
        "anchor": "    nonce = secrets.token_hex(NONCE_BYTES)\n",
        "replace": '    nonce = "x"   # SABOTAGE M3: guessable, so a body can close its own envelope\n',
    },
    {
        "id": "M4",
        "name": "read_bounded reads the whole body, then slices",
        "file": "web_tools/ceilings.py",
        "expect": "the byte-ceiling gate, and only that",
        "anchor": (
            "    budget = limit + 1\n"
            "    buf = bytearray()\n"
            "    while len(buf) < budget:\n"
            "        chunk = response.read(min(_READ_CHUNK, budget - len(buf)))\n"
            "        if not chunk:\n"
            "            break            # EOF is the only end marker on the no-Content-Length bodies\n"
            "        buf += chunk\n"
        ),
        # Every assertion about the RETURNED STRING still holds — the slice is
        # identical. Only the wire counter can tell the difference, which is the
        # whole argument for having a wire counter.
        "replace": (
            "    budget = limit + 1\n"
            "    buf = bytearray(response.read())   # SABOTAGE M4: bytes paid for, then discarded\n"
        ),
    },
    {
        "id": "M5",
        "name": "fetch ceiling raised to a number no run reaches",
        "file": "web_tools/ceilings.py",
        "expect": "the fetch-cap test",
        "anchor": "\nMAX_FETCHES = 6\n",
        # 1000, not 10**9. The suite derives its own loop bounds from this
        # constant (`for _ in range(MAX_FETCHES)`, and one loop does real
        # fetches), so an astronomical value hangs the run instead of
        # failing it — and a timeout is not a catch. 166x the real cap is
        # already a ceiling no run reaches, which is the property under test.
        "replace": "\nMAX_FETCHES = 1000   # SABOTAGE M5: 166x the real cap; no run ever reaches it\n",
    },
    {
        "id": "M6",
        "name": "neutralise returns (body, []) — defuses nothing, reports nothing",
        "file": "web_tools/neutralise.py",
        "expect": "real-fixture-payload tests and injection_detected",
        "anchor": '    newlines = [i for i, ch in enumerate(body) if ch == "\\n"]\n',
        # Inserted AFTER the None and non-str guards on purpose. A mutation that
        # also broke argument validation would go red in the type tests, and the
        # report would credit a catch to a test that never looked at a payload.
        "replace": (
            "    return body, []   # SABOTAGE M6: scan skipped, findings suppressed\n"
            '    newlines = [i for i, ch in enumerate(body) if ch == "\\n"]\n'
        ),
    },
]


# ------------------------------------------------------------ scratch tree

def _port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def require_ports_free(when, wait=PORT_WAIT):
    """Block until every fixture port is free. Abort rather than borrow.

    Waits instead of failing outright because a shared machine legitimately has
    another lab4 suite mid-run, and that is a scheduling problem, not a result.
    What it must never do is proceed while a port is held: the scratch copy
    would bind nothing, borrow the other process's servers, and report on the
    UNMUTATED tree.
    """
    deadline = time.monotonic() + wait
    while True:
        busy = [p for p in FIXTURE_PORTS if _port_open(p)]
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise SystemExit(
                "ABORT (%s): fixture ports %s still served after %ds.\n"
                "The scratch copy would BORROW them and silently test the unmutated\n"
                "tree's servers, so every verdict below would be noise. Stop whatever\n"
                "is serving them (a `python3 -m fixture_sites.origins`, another lab4\n"
                "suite, an editor test runner) and re-run." % (when, busy, wait))
        time.sleep(0.5)


def stage(label):
    """A throwaway repo skeleton deep enough for the suite's own path asserts."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="lab4-sabotage-%s-" % label,
                                         dir=SCRATCH_ROOT))
    repo = root / "repo"
    lab = repo / LAB.relative_to(REPO)
    lab.mkdir(parents=True)

    # __pycache__ excluded: copy2 preserves mtime, and a mutation that happens to
    # leave the file the same size would then be shadowed by a stale .pyc.
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(LAB / "python", lab / "python", ignore=ignore)
    shutil.copytree(LAB / "fixtures", lab / "fixtures", ignore=ignore)
    shutil.copytree(REPO / "labkit", repo / "labkit", ignore=ignore)

    # test_lab_directories_exist only asks is_dir(). Created empty rather than
    # copied: reference/ is another agent's live worktree, so reading it would
    # capture a half-written state and pin this run to someone else's progress.
    (lab / "reference" / "checkpoints").mkdir(parents=True)

    # test_sink_log_is_gitignored shells out to `git check-ignore` with cwd=REPO,
    # and test_live_tests_are_opt_in reads the root Makefile. Without both the
    # baseline is red for reasons that have nothing to do with any mutation.
    shutil.copy2(REPO / ".gitignore", repo / ".gitignore")
    shutil.copy2(REPO / "Makefile", repo / "Makefile")
    subprocess.run(["git", "init", "-q"], cwd=repo,
                   capture_output=True, check=False)

    return root, lab / "python"


def apply(py_dir, mutation):
    """Textual replace on the scratch copy. Aborts if the anchor has moved."""
    target = py_dir / mutation["file"]
    src = target.read_text(encoding="utf-8")
    hits = src.count(mutation["anchor"])
    if hits != 1:
        # Abort, do not skip. A drifted anchor means the source moved under this
        # harness, so every other anchor is suspect and no verdict in this run
        # can be trusted — including the ones that already looked fine.
        raise SystemExit(
            "ABORT: %s anchor matched %d times in %s (expected exactly 1).\n"
            "The source has moved. Fix the anchor before trusting any result here.\n"
            "  anchor: %r" % (mutation["id"], hits, mutation["file"],
                              mutation["anchor"]))
    target.write_text(src.replace(mutation["anchor"], mutation["replace"]),
                      encoding="utf-8")


# --------------------------------------------------------------- suite run

_RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
# verbosity=2 emits `FAIL: test_x (__main__.Class.test_x)` and, for a class
# fixture that blew up, `ERROR: setUpClass (__main__.Class)`.
_RED = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.M)
_CLASS_FIXTURES = ("setUpClass", "tearDownClass", "setUpModule", "tearDownModule")


def run_suite(py_dir, test_file):
    try:
        proc = subprocess.run([sys.executable, test_file.name], cwd=str(py_dir),
                              capture_output=True, text=True, timeout=SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Recorded, never raised. A mutation that hangs a suite has told us
        # something; killing the whole harness loses every mutation after it.
        return {"suite": test_file.stem, "ran": None, "red": [],
                "fixture_errors": ["<suite timed out after %ds>" % SUITE_TIMEOUT],
                "borrowed": [], "returncode": None, "tail": []}
    out = proc.stdout + proc.stderr

    borrowed = [m for m in BORROW_MARKERS if m in out]

    ran = _RAN.search(out)
    red, fixture_errors = [], []
    for kind, method, qual in _RED.findall(out):
        short = qual.split(".", 1)[1] if qual.startswith("__main__.") else qual
        if method in _CLASS_FIXTURES:
            # Not a failing test — a class that never ran. Recorded apart so it
            # cannot be counted as a catch.
            fixture_errors.append("%s:%s [%s]" % (short, method, kind))
        elif short not in red:
            # One FAIL line per failing subTest, but unittest counts the parent
            # test once. Without this a 21-test suite reports 51 red.
            red.append(short)

    return {
        "suite": test_file.stem,
        "ran": int(ran.group(1)) if ran else None,
        "red": red,
        "fixture_errors": fixture_errors,
        "borrowed": borrowed,
        "returncode": proc.returncode,
        "tail": out.strip().splitlines()[-1:] if ran is None else [],
    }


def run_all(py_dir):
    """Every suite, sequentially. Parallel is how a port gets borrowed."""
    results = []
    for test_file in sorted(py_dir.glob("test_*.py")):
        for attempt in range(BORROW_RETRIES):
            require_ports_free("before %s" % test_file.name)
            result = run_suite(py_dir, test_file)
            if not result["borrowed"]:
                break
            # A neighbour grabbed the port between the check above and the bind.
            # The result describes their tree, so it is thrown away, not reported.
            print("    retry %s: %s (attempt %d)"
                  % (test_file.name, result["borrowed"][0], attempt + 1),
                  file=sys.stderr)
            time.sleep(2)
        results.append(result)
    return results


def total(results, key):
    return sum(len(r[key]) for r in results)


def tests_ran(results):
    return sum(r["ran"] or 0 for r in results)


# ----------------------------------------------------------------- report

def report_mutation(mutation, base, mutant):
    by_suite = {r["suite"]: r for r in base}
    caught, moved, broke = [], [], []

    for r in mutant:
        b = by_suite[r["suite"]]
        caught += ["%s.%s" % (r["suite"], n) for n in r["red"]]
        if r["ran"] != b["ran"]:
            moved.append("%s: %s -> %s" % (r["suite"], b["ran"], r["ran"]))
        broke += ["%s.%s" % (r["suite"], f) for f in r["fixture_errors"]]

    red_suites = ["%s (%s red / %s ran)" % (r["suite"], len(r["red"]), r["ran"])
                  for r in mutant if r["red"] or r["fixture_errors"]]

    print("\n%s  %s" % (mutation["id"], mutation["name"]))
    print("    applied:      yes")
    print("    must turn red: %s" % mutation["expect"])
    print("    red suites:   %s" % (", ".join(red_suites) or "NONE"))
    print("    caught by (%d):" % len(caught))
    for name in caught:
        print("        %s" % name)
    if not caught:
        print("        (nothing)")
    if broke:
        print("    CLASS FIXTURE ERRORS (tests that never ran, not catches):")
        for name in broke:
            print("        %s" % name)
    if moved:
        print("    TEST COUNT MOVED — something stopped running rather than failing:")
        for line in moved:
            print("        %s" % line)
    if len(caught) == 1:
        print("    NOTE: a single test is the entire verification of this control.")
    print("    verdict:      %s" % ("CAUGHT" if caught else "*** SURVIVED ***"))

    return {"caught": caught, "moved": moved, "fixture_errors": broke}


def main():
    if not os.path.isdir(SCRATCH_ROOT):
        raise SystemExit("ABORT: %s does not exist" % SCRATCH_ROOT)
    require_ports_free("startup")

    print("lab: %s" % LAB)
    print("scratch root: %s" % SCRATCH_ROOT)

    scratch, py_dir = stage("baseline")
    try:
        base = run_all(py_dir)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    borrowed = [m for r in base for m in r["borrowed"]]
    if borrowed:
        raise SystemExit(
            "ABORT: the baseline BORROWED fixture servers from another process:\n"
            "  %s\nNothing measured here describes the scratch copy." %
            "\n  ".join(sorted(set(borrowed))))

    base_red = total(base, "red")
    base_fixture = total(base, "fixture_errors")
    print("\nbaseline: %d tests across %d suites, %d red, %d class-fixture errors"
          % (tests_ran(base), len(base), base_red, base_fixture))
    for r in base:
        print("    %-24s %s" % (r["suite"], r["ran"]))
    if base_red or base_fixture:
        for r in base:
            for n in r["red"] + r["fixture_errors"]:
                print("    BASELINE RED: %s.%s" % (r["suite"], n))
        raise SystemExit(
            "ABORT: the baseline is not green. A mutation's red is only evidence\n"
            "against a green baseline; fix the scratch staging first.")

    outcomes = {}
    for mutation in MUTATIONS:
        scratch, py_dir = stage(mutation["id"].lower())
        try:
            apply(py_dir, mutation)
            mutant = run_all(py_dir)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        borrowed = [m for r in mutant for m in r["borrowed"]]
        if borrowed:
            raise SystemExit(
                "ABORT: %s BORROWED fixture servers from another process:\n  %s\n"
                "That run exercised someone else's tree; its verdict is void."
                % (mutation["id"], "\n  ".join(sorted(set(borrowed)))))

        outcomes[mutation["id"]] = report_mutation(mutation, base, mutant)

    survived = [k for k, v in outcomes.items() if not v["caught"]]
    moved = [k for k, v in outcomes.items() if v["moved"]]
    lonely = [k for k, v in outcomes.items() if len(v["caught"]) == 1]

    print("\n" + "=" * 70)
    print("summary: %d mutations, %d caught, %d survived, %d with a moved test count"
          % (len(MUTATIONS), len(MUTATIONS) - len(survived), len(survived), len(moved)))
    if lonely:
        print("single-test verification (one failure away from unverified): %s"
              % ", ".join(lonely))
    if survived:
        print("SURVIVED — the control is taught but not verified: %s"
              % ", ".join(survived))
    if moved:
        print("COUNT MOVED — tests stopped running rather than failing: %s"
              % ", ".join(moved))

    # A moved count is as disqualifying as a survivor: it means the run did not
    # measure what it claims to have measured.
    return 1 if (survived or moved) else 0


if __name__ == "__main__":
    sys.exit(main())
