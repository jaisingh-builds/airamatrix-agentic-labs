"""Configuration, read once from the environment (or repo-root .env)."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load repo-root .env without a third-party dependency."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


class Config:
    """Where to call, as what, and with what ceiling."""

    def __init__(self) -> None:
        _load_dotenv()
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = os.environ.get("LAB_MODEL", "claude-sonnet")
        self.max_steps = int(os.environ.get("LAB_MAX_STEPS", "8"))
        self.budget_usd = float(os.environ.get("LAB_BUDGET_USD", "0.50"))

    def require(self) -> "Config":
        missing = [n for n, v in (("ANTHROPIC_BASE_URL", self.base_url),
                                  ("ANTHROPIC_AUTH_TOKEN", self.api_key)) if not v]
        if missing:
            raise SystemExit(
                "Missing: " + ", ".join(missing) + "\n"
                "Copy .env.example to .env and paste the gateway URL and your key,\n"
                "then re-run. See setup/05-verify.md."
            )
        return self
