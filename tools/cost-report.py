#!/usr/bin/env python3
"""Show spend. With your own key: your spend. With the master key: everyone's."""
import json, os, sys, urllib.request, urllib.error, pathlib


def load_env() -> None:
    for parent in pathlib.Path(__file__).resolve().parents:
        env = parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            return


def get(path: str, key: str) -> dict:
    req = urllib.request.Request(f"{os.environ['ANTHROPIC_BASE_URL'].rstrip('/')}{path}",
                                 headers={"authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    load_env()
    key = os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if not key:
        raise SystemExit("No key. Configure .env first.")
    try:
        info = get("/key/info", key)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"could not read spend ({e.code}): {e.read().decode()[:200]}")
    data = info.get("info", info)
    spend = float(data.get("spend") or 0)
    budget = data.get("max_budget")
    print(f"  user     {data.get('user_id', '?')}")
    print(f"  spend    ${spend:.4f}")
    if budget:
        print(f"  budget   ${float(budget):.2f} per {data.get('budget_duration', 'day')}")
        print(f"  left     ${float(budget) - spend:.4f}")
    models = data.get("models")
    if models:
        print(f"  models   {', '.join(models)}")


if __name__ == "__main__":
    main()
