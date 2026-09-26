package com.airamatrix.capstone;

import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

/** common/trace_view.py in Java: print a JSONL trace as a tree (x = failed span). Both read the same files. */
public final class TraceView {
    private TraceView() {}

    public static void render(Path file, PrintStream out) throws Exception {
        List<JsonNode> spans = new ArrayList<>();
        for (String l : Files.readAllLines(file)) if (!l.isBlank()) spans.add(Contracts.JSON.readTree(l));
        Map<String, List<JsonNode>> kids = new LinkedHashMap<>();
        for (JsonNode s : spans) kids.computeIfAbsent(s.path("parent_id").isNull() ? "" : s.path("parent_id").asText(), k -> new ArrayList<>()).add(s);
        kids.values().forEach(v -> v.sort(Comparator.comparingDouble(s -> s.path("start").asDouble())));
        walk(kids, "", 0, out);
    }

    private static void walk(Map<String, List<JsonNode>> kids, String parent, int depth, PrintStream out) {
        for (JsonNode s : kids.getOrDefault(parent, List.of())) {
            StringBuilder attrs = new StringBuilder();
            s.path("attrs").fields().forEachRemaining(e -> {
                String v = e.getValue().toString();
                attrs.append(' ').append(e.getKey()).append('=').append(v.length() > 70 ? v.substring(0, 70) : v);
            });
            String pad = "  ".repeat(depth);
            out.println(pad + ("error".equals(s.path("status").asText()) ? "x " : "- ") + s.path("name").asText() + "  "
                    + s.path("duration_ms").asLong() + " ms" + attrs);
            if (!s.path("error").isNull() && !s.path("error").asText().isEmpty()) out.println(pad + "    ERROR " + s.path("error").asText());
            walk(kids, s.path("span_id").asText(), depth + 1, out);
        }
    }
}
