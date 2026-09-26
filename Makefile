# AiraMatrix Agentic Labs — one entry point for every day.
SHELL := /bin/bash
LAB1 := day1-foundations/lab1-bare-metal-loop
LAB4 := day3-integration-security/lab4-untrusted-web

.PHONY: help doctor conformance test test-python test-node test-java lab1 lab4-test day3-test clean cost

help:
	@echo "make doctor       - check your machine is ready (run this first)"
	@echo "make conformance  - check the gateway itself is healthy (trainer)"
	@echo "make test         - run every lab's offline checks"
	@echo "make lab1         - run Lab 1.1 (python)"
	@echo "make day3-test    - run every Day 3 offline suite"
	@echo "make day4-test    - run every Day 4 offline suite (needs the Day 4 venv)"
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

# Removes each control in a scratch copy and re-runs the suite. Green here means
# a control the lab teaches but has never verified, so it exits non-zero.
.PHONY: lab4-sabotage
lab4-sabotage:
	@echo "=== lab4: sabotage (control removal) ==="
	@python3 $(LAB4)/tools/sabotage.py

D3 := day3-integration-security
day3-test:
	@echo "=== day3: aira-ops ===" && cd $(D3)/aira-ops && python3 -m unittest -q test_aira_ops
	@echo "=== day3: 4.2 mcp server ===" && cd $(D3)/lab4-2-mcp-server && python3 -m unittest -q test_mcp_server
	@echo "=== day3: 4.3 agent service ===" && cd $(D3)/lab4-3-agent-service && python3 -m unittest -q test_stream test_service
	@$(MAKE) --no-print-directory lab4-test

D4 := day4-orchestration-evals-cicd
.PHONY: day4-test day4-starters day4-java-test
day4-test:
	@echo "=== day4: common (spans) ===" && cd $(D4)/common && python3 -m unittest -q test_spans
	@echo "=== day4: 5.1 handoff ===" && cd $(D4)/lab5-1-handoff && python3 -m unittest -q test_pipeline test_graph
	@echo "=== day4: 5.2 evals ===" && cd $(D4)/lab5-2-evals && python3 -m unittest -q test_graders test_judge
	@echo "=== day4: 5.3 pr review ===" && cd $(D4)/lab5-3-pr-review && python3 -m unittest -q test_review

# Day 4, Java / Spring Boot track: offline tests of common + the three lab modules (no model, no cost).
day4-java-test:
	mvn -q -pl $(D4)/java/common,$(D4)/java/lab51,$(D4)/java/lab52,$(D4)/java/lab53 -am test

# Your TODO progress: these FAIL until the starters are finished.
day4-starters:
	-@cd $(D4)/lab5-1-handoff && LAB51_TARGET=starter python3 -m unittest -q test_pipeline
	-@cd $(D4)/lab5-2-evals && LAB52_TARGET=starter python3 -m unittest -q test_graders
	-@cd $(D4)/lab5-3-pr-review && LAB53_TARGET=starter python3 -m unittest -q test_review
