package com.airamatrix.capstone;

import java.util.function.Supplier;

/**
 * A hook for the runtime's OpenTelemetry GenAI spans (execute_tool). Local mode uses {@link #NONE}:
 * the JSONL trace (common Spans) is written either way.
 */
public interface Telemetry {
    Telemetry NONE = new Telemetry() {};

    default Tools.Result tool(String name, String argumentsJson, Supplier<Tools.Result> call) { return call.get(); }
}
