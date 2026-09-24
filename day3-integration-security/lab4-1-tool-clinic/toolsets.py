"""
Two toolsets over the SAME aira-ops API. Only the interface differs.

BAD is what gets written in a hurry: three tools, vague names, one-letter
parameters, a tool that does two jobs, errors swallowed to the word "error".
GOOD is the same capability designed for a model to use.

Lab 4.1: you rewrite BAD into your own version in mine.py and measure it.
"""
import json, re

# ----------------------------------------------------------------------- BAD
BAD = [
    {"name": "tickets",
     "description": "Ticket stuff.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "acct",
     "description": "Get account.",
     "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}},
    {"name": "cfg",
     "description": "Config.",
     "input_schema": {"type": "object", "properties": {"k": {"type": "string"}, "v": {}}, "required": ["k"]}},
]

def run_bad(api, name, args):
    """What a hurried implementation does: guess intent, swallow detail."""
    if name == "tickets":
        q = str(args.get("q", ""))
        if re.fullmatch(r"T-\d{4}", q):
            s, b = api("GET", f"/tickets/{q}")
        else:
            from urllib.parse import quote
            s, b = api("GET", f"/tickets?q={quote(q)}")
        return (json.dumps(b), False) if s == 200 else ("error", True)
    if name == "acct":
        s, b = api("GET", f"/accounts/{args.get('a','')}")
        return (json.dumps(b), False) if s == 200 else ("error", True)
    if name == "cfg":
        s, b = api("GET", f"/config/{args.get('k','')}")
        return (json.dumps(b), False) if s == 200 else ("error", True)
    return ("error", True)

# ---------------------------------------------------------------------- GOOD
GOOD = [
    {"name": "search_tickets",
     "description": ("Find support tickets by status, priority, account or words in the title/body. "
                     "Returns a short list (id, title, status, priority, account_id, assignee). "
                     "Use this whenever you do not already have a ticket id; use get_ticket for the "
                     "full body and comments."),
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["open", "in_progress", "resolved", "closed"]},
         "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"], "description": "P1 is most urgent"},
         "account_id": {"type": "string", "pattern": "^ACC-\\d{4}$", "description": "e.g. ACC-1001"},
         "query": {"type": "string", "description": "Words to match in title or body, e.g. 'DICOM'"}}}},
    {"name": "get_ticket",
     "description": "Full ticket by id: body, status, assignee and every comment (resolutions are in comments).",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "pattern": "^T-\\d{4}$", "description": "Ticket id, e.g. T-1001"}},
         "required": ["id"]}},
    {"name": "lookup_account",
     "description": ("Customer account by id: name, tier, region, contracted SLA in minutes, and the "
                     "number of open tickets. Ids look like ACC-1001; this does not search by name."),
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "pattern": "^ACC-\\d{4}$", "description": "e.g. ACC-1001"}},
         "required": ["id"]}},
    {"name": "get_config",
     "description": ("Read platform configuration. Pass a key (e.g. 'ingest.rush_slide_limit') for one "
                     "value, or no key to list every key with its value and description."),
     "input_schema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "Dotted key, e.g. alerts.ingest_latency_minutes"}}}},
]

def run_good(api, name, args):
    """Same API. Errors pass through with their code, message and hint."""
    if name == "search_tickets":
        from urllib.parse import urlencode
        p = {k: v for k, v in (("status", args.get("status")), ("priority", args.get("priority")),
                               ("account_id", args.get("account_id")), ("q", args.get("query"))) if v}
        s, b = api("GET", "/tickets?" + urlencode(p))
    elif name == "get_ticket":
        s, b = api("GET", f"/tickets/{args.get('id','')}")
    elif name == "lookup_account":
        s, b = api("GET", f"/accounts/{args.get('id','')}")
    elif name == "get_config":
        k = args.get("key")
        s, b = api("GET", f"/config/{k}" if k else "/config")
    else:
        return (json.dumps({"error": {"code": "unknown_tool", "message": name,
                                      "hint": "Available: " + ", ".join(t['name'] for t in GOOD)}}), True)
    return (json.dumps(b), s >= 400)

TOOLSETS = {"bad": (BAD, run_bad), "good": (GOOD, run_good)}
