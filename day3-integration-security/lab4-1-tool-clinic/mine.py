"""
YOUR toolset for Lab 4.1. Start from the BAD one (copied below) and fix it.

Run:   python3 clinic.py --tools bad mine

What to change - each is worth measuring on its own:
  1. Names that say what the tool does, and one job per tool.
  2. Descriptions that say what it returns, when to use it, and when NOT to.
  3. Typed, named parameters: enums for closed sets, patterns for ids, examples.
  4. Errors the model can act on: pass through code, message and hint.
  5. A way to DISCOVER valid values (e.g. list config keys) instead of guessing.

Keep run() honest: it must call the real API through `api`, same as the others.
"""
import json, re
from toolsets import BAD, run_bad

TOOLS = json.loads(json.dumps(BAD))   # TODO: rewrite these


def run(api, name, args):
    return run_bad(api, name, args)   # TODO: route your tools to the API
