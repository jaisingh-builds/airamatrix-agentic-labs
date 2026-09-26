"""Where things are in the labs checkout, and the two repo modules this solution reuses.

LABS_REPO overrides; otherwise the checkout this file lives in (or the working directory upwards).
Imports labkit (Config, GatewayClient, BudgetGuard) and common/spans.py (Tracer, redact) - the repo's
own plumbing, standard library only. In the AgentCore package both are copied next to this code.
"""
import os, platform, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _is_root(d):
    return (d / "labkit").is_dir() and (d / "day3-integration-security").is_dir()


def root():
    env = os.environ.get("LABS_REPO", "").strip()
    if env:
        return Path(env).resolve()
    for d in [HERE, *HERE.parents, Path.cwd(), *Path.cwd().parents]:
        if _is_root(d):
            return d
    raise RuntimeError("run from inside the airamatrix-agentic-labs checkout, or set LABS_REPO")


def ops_script():
    return root() / "day3-integration-security" / "aira-ops" / "aira_ops.py"


def solution():
    """reference-solution/ - golden/ and fixtures/ are shared by the Java, Python and Node solutions."""
    return HERE.parents[1]


def python_dir():
    """reference-solution/python/ - this language's results/, samples/ and out/."""
    return HERE.parent


def python_exe():
    """python3, or python on Windows, unless LAB_PYTHON says otherwise."""
    p = os.environ.get("LAB_PYTHON", "").strip()
    if p:
        return p
    return "python" if platform.system().lower().startswith("win") else "python3"


def _add_paths():
    try:
        import spans, agentic_core  # noqa: F401  (already importable, e.g. inside the AgentCore package)
        return
    except ImportError:
        pass
    r = root()
    for p in (r / "labkit" / "python", r / "day4-orchestration-evals-cicd" / "common"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


_add_paths()
import spans  # noqa: E402  common/spans.py
from agentic_core.budget import BudgetGuard, BudgetExceeded  # noqa: E402,F401
