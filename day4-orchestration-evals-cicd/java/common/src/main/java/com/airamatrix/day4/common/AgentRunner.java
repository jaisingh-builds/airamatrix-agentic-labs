package com.airamatrix.day4.common;

import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * Turns (stage, system, prompt, schema) into a validated JSON object. Same contract as the
 * Python Runner: the pipeline, the eval harness and the tests only ever see this interface, so
 * every control can be tested with a fake runner and no model.
 */
public interface AgentRunner {

    AgentResult run(String stage, String system, String prompt, JsonNode schema);

    record ToolCall(String name, JsonNode input) {}

    /** output: the validated object. toolOk: per call, true / false (the tool returned an error). */
    record AgentResult(JsonNode output, double costUsd, List<ToolCall> toolCalls, int turns, List<Boolean> toolOk) {
        public AgentResult(JsonNode output, double costUsd, List<ToolCall> toolCalls, int turns) {
            this(output, costUsd, toolCalls, turns, toolCalls.stream().map(t -> (Boolean) null).toList());
        }
    }

    /** A stage failed. costUsd is what the failed attempt still cost - failure costs money too. */
    class RunnerException extends RuntimeException {
        public final double costUsd;
        public final int turns;
        public RunnerException(String message, double costUsd, int turns) {
            super(message);
            this.costUsd = costUsd;
            this.turns = turns;
        }
        public RunnerException(String message) { this(message, 0, 0); }
    }
}
