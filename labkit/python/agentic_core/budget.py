"""A spend ceiling that refuses the next call, rather than warning after it.

The gateway enforces a per-participant daily budget server-side. This is the
client-side half: it stops a runaway loop before it makes the call, and gives
you a number to print at the end of a lab.
"""


class BudgetExceeded(RuntimeError):
    pass


# USD per token. Mirrors the gateway's model_info block.
PRICES = {
    "claude-sonnet": (0.000002, 0.00001),
    "claude-opus":   (0.000005, 0.000025),
    "claude-haiku":  (0.000001, 0.000005),
}


class BudgetGuard:
    def __init__(self, limit_usd: float, model: str = "claude-sonnet") -> None:
        self.limit = limit_usd
        self.model = model
        self.spent = 0.0
        self.calls = 0

    def check(self) -> None:
        """Call BEFORE each request."""
        if self.spent >= self.limit:
            raise BudgetExceeded(
                f"Budget ceiling hit: ${self.spent:.4f} of ${self.limit:.2f} "
                f"after {self.calls} calls. Raise LAB_BUDGET_USD to continue."
            )

    def record(self, usage: dict) -> float:
        """Call AFTER each response. Returns the cost of that call."""
        rate_in, rate_out = PRICES.get(self.model, PRICES["claude-sonnet"])
        cached = usage.get("cache_read_input_tokens", 0) or 0
        fresh = usage.get("input_tokens", 0) or 0
        written = usage.get("cache_creation_input_tokens", 0) or 0
        cost = (fresh * rate_in
                + cached * rate_in * 0.1
                + written * rate_in * 1.25
                + (usage.get("output_tokens", 0) or 0) * rate_out)
        self.spent += cost
        self.calls += 1
        return cost

    def summary(self) -> str:
        return f"{self.calls} calls, ${self.spent:.4f} of ${self.limit:.2f}"
