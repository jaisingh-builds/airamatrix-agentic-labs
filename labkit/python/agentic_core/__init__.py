"""Shared lab infrastructure — NOT an agent framework.

This package gives you the boring, error-prone plumbing: an HTTP client for the
gateway, run tracing, cost accounting and a budget ceiling.

It deliberately does NOT contain an agent loop or a tool registry.
Writing those is Lab 1.1 — that is the whole point of the session.
"""
from .config import Config
from .client import GatewayClient, GatewayError
from .tracer import Tracer
from .budget import BudgetGuard, BudgetExceeded

__all__ = ["Config", "GatewayClient", "GatewayError", "Tracer",
           "BudgetGuard", "BudgetExceeded"]
