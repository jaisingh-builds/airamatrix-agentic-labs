# AiraMatrix Agentic Labs — one entry point for every day.
SHELL := /bin/bash
LAB1 := day1-foundations/lab1-bare-metal-loop
LAB4 := day3-integration-security/lab4-untrusted-web

.PHONY: help doctor conformance test test-python test-node test-java lab1 lab4-test clean cost

help:
	@echo "make doctor       - check your machine is ready (run this first)"
	@echo "make conformance  - check the gateway itself is healthy (trainer)"
	@echo "make test         - run every lab's offline checks"
	@echo "make lab1         - run Lab 1.1 (python)"
	@echo "make cost         - show your spend so far"
	@echo ""
	@echo "Live checks cost ~1 cent and are opt-in:  LAB_LIVE=1 make test"

doctor:
	@bash bootstrap/doctor.sh

conformance:
	@bash bootstrap/gateway-conformance.sh

test: test-python test-node test-java

test-python:
	@echo "=== python ==="
	@cd $(LAB1)/python && python3 test_agent.py

test-node:
	@echo "=== node ==="
	@cd $(LAB1)/node && node --test

test-java:
	@echo "=== java ==="
	@mvn -q test -pl $(LAB1)/java -am

lab1:
	@cd $(LAB1)/python && python3 agent.py

cost:
	@python3 tools/cost-report.py

clean:
	@mvn -q clean 2>/dev/null || true
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"

lab4-test:
	@echo "=== lab4: untrusted web ==="
	@cd $(LAB4)/python && for t in test_*.py; do \
	  [ -e "$$t" ] || continue; echo "--- $$t"; python3 "$$t" || exit 1; done
