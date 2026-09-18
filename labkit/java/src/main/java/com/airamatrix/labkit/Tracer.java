package com.airamatrix.labkit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.nio.file.*;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** One JSON line per agent step. Day 1 reads these; Day 4 debugs from them. */
public final class Tracer {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    public final String runId;
    public final Path path;
    private final long t0 = System.currentTimeMillis();

    public Tracer(String name) {
        this.runId = name + "-" + UUID.randomUUID().toString().substring(0, 8);
        Path base = Paths.get("").toAbsolutePath();
        for (int i = 0; i < 8 && base != null; i++, base = base.getParent()) {
            if (Files.isDirectory(base.resolve("labkit"))) break;
        }
        if (base == null) base = Paths.get("").toAbsolutePath();
        Path dir = base.resolve("traces");
        try { Files.createDirectories(dir); } catch (IOException ignored) { }
        this.path = dir.resolve(runId + ".jsonl");
    }

    public void emit(String kind, Map<String, ?> fields) {
        ObjectNode record = MAPPER.createObjectNode();
        record.put("run_id", runId);
        record.put("t", (System.currentTimeMillis() - t0) / 1000.0);
        record.put("kind", kind);
        fields.forEach((k, v) -> record.set(k, MAPPER.valueToTree(v)));
        try {
            Files.writeString(path, MAPPER.writeValueAsString(record) + "\n",
                StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException ignored) { }
    }

    public void step(int n, String stopReason, String text, List<String> tools) {
        emit("step", Map.of("n", n, "stop_reason", String.valueOf(stopReason),
            "text", text == null ? "" : text.substring(0, Math.min(400, text.length())),
            "tools", tools));
    }

    public void tool(String name, Object args, String result, boolean ok) {
        emit("tool_result", Map.of("name", name, "args", args, "ok", ok,
            "result", result.substring(0, Math.min(400, result.length()))));
    }
}
