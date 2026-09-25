# Team <name>

**Members:**
**Branch:**

## Problem
Who has it, how often, what it costs today. One paragraph.

## Pattern
Single agent / sequential pipeline / supervisor-router / evaluator-reviewer - and why this one.
A diagram is welcome (`python3 ../lab5-1-handoff/graph_langgraph.py --print-graph` shows the format).

## Tools and access
| Tool / MCP server | Read or write | Token / scope |
|---|---|---|

## Guardrail
What it stops, where it lives in the code (file:function), and how we show it refusing.

## Human approval point
Where a person decides, what is recorded (who, why), what refuses to run without it.

## Eval
Golden cases (≥ 5, from real work): where each came from.
Checks: outcome / trajectory. Result: __ / __ passed over __ runs, cost $__.

## Trace
File: `traces/...jsonl`. What it shows (ideally a failure and how we found the cause).

## What we'd do next
