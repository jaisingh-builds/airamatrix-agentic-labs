package com.airamatrix.day4.lab51;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.AgentRunner;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

/** Plays the agents from a script: stage -> outputs (JSON text) or exceptions. Same as test_pipeline.FakeRunner. */
class FakeRunner implements AgentRunner {
    final Map<String, Deque<Object>> script = new HashMap<>();
    final List<String> calls = new ArrayList<>();

    FakeRunner on(String stage, Object... outputs) {
        script.computeIfAbsent(stage, k -> new ArrayDeque<>()).addAll(List.of(outputs));
        return this;
    }

    @Override
    public AgentResult run(String stage, String system, String prompt, JsonNode schema) {
        calls.add(stage);
        Deque<Object> q = script.get(stage);
        Object next = q == null ? null : q.poll();
        if (next == null) throw new IllegalStateException("FakeRunner: nothing scripted for " + stage);
        if (next instanceof RuntimeException e) throw e;
        JsonNode out = json(next.toString());
        return new AgentResult(out, 0.03,
                List.of(new ToolCall("get_config", json("{\"key\": \"ingest.max_concurrent_jobs\"}"))), 4);
    }

    static JsonNode json(String s) {
        try {
            return Contracts.JSON.readTree(s);
        } catch (Exception e) {
            throw new IllegalArgumentException(e);
        }
    }

    // ---- the same scripted outputs as test_pipeline.py
    static final String GOOD = """
        {"diagnosis": "ingest.max_concurrent_jobs was cut from 16 to 4, capping throughput below demand.",
         "evidence": ["T-1001 on-call comment suspects the concurrency cap",
                      "config ingest.max_concurrent_jobs = 4 (version 1)"],
         "confidence": "high", "risks": ["Lowered on purpose during a memory investigation"],
         "proposed_change": {"action": "update_config", "key": "ingest.max_concurrent_jobs",
                             "value": 8, "expected_version": 1}}""";
    static final String APPROVE = """
        {"verdict": "approve", "checks": [{"claim": "value is 4", "verified": true, "source": "get_config"}],
         "reasons": ["claims verified; halfway step is proportionate"]}""";
    static final String REVISE = """
        {"verdict": "revise", "checks": [{"claim": "value is 4", "verified": true, "source": "get_config"}],
         "reasons": ["facts are right, but 16 is more than the evidence supports"],
         "safer_alternative": "raise 4 -> 8 and watch worker memory"}""";
    static final String BLOCK = """
        {"verdict": "block", "checks": [{"claim": "value is 4", "verified": true}],
         "reasons": ["overrides a deliberate change with no evidence the memory issue is fixed"]}""";

    /** GOOD with a different proposed_change. */
    static String goodWith(String change) {
        var n = (com.fasterxml.jackson.databind.node.ObjectNode) json(GOOD);
        n.set("proposed_change", json(change));
        return n.toString();
    }
}
