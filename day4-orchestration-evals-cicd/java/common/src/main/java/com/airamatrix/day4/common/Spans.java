package com.airamatrix.day4.common;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Tracing spans - one JSON line per span, the SAME record format as common/spans.py, so
 * `python3 ../common/trace_view.py --latest lab5-1-...` prints a Java run too.
 *
 * <pre>
 *   Spans tr = new Spans("lab5-1", runId);
 *   try (Spans.Span s = tr.span("stage.investigate", Map.of("account", "ACC-1001"))) {
 *       ...
 *       s.set("cost_usd", 0.03).set("tool_calls", 4);
 *   }                       // an exception inside: call s.fail(e) before rethrowing
 * </pre>
 *
 * Same rules as the Python sink, in the same order: only ALLOWED attribute names are written
 * (anything else becomes "[dropped]"); tool input keeps identifiers and replaces free text with
 * its length; every value is REDACTED at write time (bearer tokens, sk- keys, 32+ hex, the value
 * of any secret-named environment variable); long strings are cut to 600 chars.
 */
public final class Spans {
    public static final int MAX_ATTR = 600;
    public static final Pattern SECRET_NAMES =
            Pattern.compile("(token|secret|passw(or)?d|credential|api_?key|private_?key|auth|_key$)", Pattern.CASE_INSENSITIVE);
    private static final List<Pattern> PATTERNS = List.of(
            Pattern.compile("(?i)bearer\\s+[A-Za-z0-9._\\-]{8,}"),
            Pattern.compile("sk-[A-Za-z0-9_\\-]{8,}"),
            Pattern.compile("\\b[0-9a-f]{32,}\\b"));
    private static final List<String> REPLACEMENTS = List.of("Bearer [REDACTED]", "sk-[REDACTED]", "[REDACTED-HEX]");
    public static final Set<String> ALLOWED_ATTRS = Set.of("account", "action", "approver", "attempt", "base", "case",
            "cost_usd", "decision", "denials", "diff_bytes", "dropped", "exit_code", "files", "head", "http_status",
            "input", "kept", "ok", "op_id", "override", "reason", "replayed", "stage", "tool", "tool_calls", "turns", "verdict");
    private static final Pattern ID_LIKE = Pattern.compile("^[A-Za-z0-9._:/-]{1,64}$");

    public final String traceId;
    public final Path path;
    private final Deque<Span> stack = new ArrayDeque<>();

    public Spans(String name, String traceId) {
        this.traceId = traceId != null ? traceId : UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        String dir = System.getenv("LAB_TRACE_DIR");
        Path root = dir != null ? Paths.get(dir) : repoRoot().resolve("traces");
        try { Files.createDirectories(root); } catch (IOException e) { throw new IllegalStateException(e); }
        this.path = root.resolve(name + "-" + this.traceId + ".jsonl");
    }

    /** The repo root: the nearest parent directory holding .env.example or labkit/. */
    public static Path repoRoot() {
        Path p = Paths.get("").toAbsolutePath();
        for (Path d = p; d != null; d = d.getParent()) {
            if (Files.exists(d.resolve(".env.example")) || Files.exists(d.resolve("labkit"))) return d;
        }
        return p;
    }

    public Span span(String name, Map<String, ?> attrs) {
        Span s = new Span(name, stack.isEmpty() ? null : stack.peek().spanId, attrs);
        stack.push(s);
        return s;
    }

    public Span span(String name) { return span(name, Map.of()); }

    /** A zero-duration span: a tool call, a decision, an approval. */
    public void event(String name, Map<String, ?> attrs) {
        try (Span s = span(name, attrs)) { /* nothing */ }
    }

    public final class Span implements AutoCloseable {
        final String name, spanId, parentId;
        final Map<String, Object> attrs = new LinkedHashMap<>();
        final double start = System.currentTimeMillis() / 1000.0;
        String status = "ok", error;

        Span(String name, String parentId, Map<String, ?> attrs) {
            this.name = name; this.parentId = parentId;
            this.spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 12);
            this.attrs.putAll(attrs);
        }

        public Span set(String key, Object value) { attrs.put(key, value); return this; }

        /** Redact FIRST, then cut: truncation is not redaction. */
        public void fail(Object err) { status = "error"; error = (String) redact(String.valueOf(err), 300); }

        @Override
        public void close() {
            stack.remove(this);
            Map<String, Object> rec = new LinkedHashMap<>();
            rec.put("trace_id", traceId);
            rec.put("span_id", spanId);
            rec.put("parent_id", parentId);
            rec.put("name", name);
            rec.put("start", Math.round(start * 1000) / 1000.0);
            rec.put("duration_ms", Math.round(System.currentTimeMillis() - start * 1000));
            rec.put("status", status);
            rec.put("error", error);
            rec.put("attrs", redact(minimise(attrs), MAX_ATTR));
            try {
                Files.writeString(path, Contracts.JSON.writeValueAsString(rec) + "\n", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException e) {
                System.err.println("trace write failed: " + e.getMessage());
            }
        }
    }

    public static Map<String, Object> minimise(Map<String, Object> attrs) {
        Map<String, Object> out = new LinkedHashMap<>();
        attrs.forEach((k, v) -> {
            if (!ALLOWED_ATTRS.contains(k)) {
                out.put(k, "[dropped]");
            } else if (k.equals("input") && v instanceof Map<?, ?> m) {
                Map<String, Object> in = new LinkedHashMap<>();
                m.forEach((ik, iv) -> in.put(String.valueOf(ik), iv instanceof String str && !ID_LIKE.matcher(str).matches()
                        ? "[text: " + str.length() + " chars]" : iv));
                out.put(k, in);
            } else {
                out.put(k, v);
            }
        });
        return out;
    }

    /** Mask secrets in any JSON-able value. limit &lt;= 0 keeps the full length (text that is sent on). */
    public static Object redact(Object value, int limit) {
        if (value instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, v) -> out.put(String.valueOf(k),
                    SECRET_NAMES.matcher(String.valueOf(k)).find() ? "[REDACTED]" : redact(v, limit)));
            return out;
        }
        if (value instanceof List<?> l) return l.stream().map(v -> redact(v, limit)).toList();
        if (!(value instanceof String s)) return value;
        for (Map.Entry<String, String> e : System.getenv().entrySet()) {
            if (SECRET_NAMES.matcher(e.getKey()).find() && e.getValue().length() >= 8) s = s.replace(e.getValue(), "[REDACTED]");
        }
        for (int i = 0; i < PATTERNS.size(); i++) s = PATTERNS.get(i).matcher(s).replaceAll(Matcher.quoteReplacement(REPLACEMENTS.get(i)));
        if (limit > 0 && s.length() > limit) s = s.substring(0, limit) + "...[+" + (s.length() - limit) + " chars]";
        return s;
    }

    public static String redact(String s) { return (String) redact(s, 0); }
}
