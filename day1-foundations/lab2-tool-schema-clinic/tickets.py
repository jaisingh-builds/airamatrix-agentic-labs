"""A tiny ticket system the agent acts on.

Success in this lab is measured by what actually changed in this store - not by
what the agent said it did. An agent that claims success while changing nothing
is the single most common failure in production, and prose-based grading misses it.
"""
from copy import deepcopy

SEED = {
    "TKT-1001": {"title": "Login times out after SSO redirect", "priority": "medium",
                 "status": "open", "assignee": "platform"},
    "TKT-1002": {"title": "Export to CSV drops the last row", "priority": "low",
                 "status": "open", "assignee": "reporting"},
    "TKT-1003": {"title": "Ingest queue backing up in ap-south-1", "priority": "high",
                 "status": "open", "assignee": "platform"},
    "TKT-1004": {"title": "Password reset email never arrives", "priority": "medium",
                 "status": "closed", "assignee": "identity"},
}

VALID_PRIORITIES = ["low", "medium", "high", "critical"]
VALID_STATUSES = ["open", "in_progress", "blocked", "closed"]


class Store:
    def __init__(self) -> None:
        self.tickets = deepcopy(SEED)
        self.calls: list[tuple[str, dict]] = []

    # ---------------------------------------------------------------- tools
    def find_ticket(self, query: str) -> str:
        hits = [f"{tid}: {t['title']} (priority={t['priority']}, status={t['status']})"
                for tid, t in self.tickets.items()
                if query.lower() in t["title"].lower() or query.lower() == tid.lower()]
        if not hits:
            return (f"no ticket matches {query!r}. Try a word from the title, "
                    f"or a ticket id like TKT-1001.")
        return "\n".join(hits)

    def set_priority(self, ticket_id: str, priority: str) -> str:
        if ticket_id not in self.tickets:
            return (f"ERROR: no such ticket {ticket_id!r}. "
                    f"Known ids: {', '.join(self.tickets)}")
        if priority not in VALID_PRIORITIES:
            return (f"ERROR: {priority!r} is not a valid priority. "
                    f"Use one of: {', '.join(VALID_PRIORITIES)}")
        self.tickets[ticket_id]["priority"] = priority
        return f"{ticket_id} priority set to {priority}"

    def set_status(self, ticket_id: str, status: str) -> str:
        if ticket_id not in self.tickets:
            return f"ERROR: no such ticket {ticket_id!r}. Known ids: {', '.join(self.tickets)}"
        if status not in VALID_STATUSES:
            return f"ERROR: {status!r} is not a valid status. Use one of: {', '.join(VALID_STATUSES)}"
        self.tickets[ticket_id]["status"] = status
        return f"{ticket_id} status set to {status}"

    def dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        self.calls.append((name, args))
        fn = {"find_ticket": self.find_ticket,
              "set_priority": self.set_priority,
              "set_status": self.set_status}.get(name)
        if fn is None:
            return f"unknown tool: {name}", False
        try:
            out = fn(**args)
            return out, not out.startswith("ERROR")
        except TypeError as exc:
            return f"ERROR: wrong arguments for {name}: {exc}", False
