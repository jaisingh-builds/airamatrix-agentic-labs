package com.airamatrix.lab1;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.stream.Collectors;

/**
 * The three tools for Lab 1.1, and their schemas.
 *
 * The schema IS the prompt. The model never sees your implementation - only the
 * name, the description and the JSON Schema. Lab 1.2 proves that by breaking them.
 */
public final class Tools {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Set<String> ALLOWED_HOSTS = Set.of("127.0.0.1", "localhost");

    public static class ToolError extends RuntimeException {
        public ToolError(String message) { super(message); }
    }

    /** Lab workspace directory, found by walking up to the lab root. */
    public static Path workspace() {
        Path dir = Paths.get("").toAbsolutePath();
        for (int i = 0; i < 8 && dir != null; i++, dir = dir.getParent()) {
            Path candidate = dir.resolve("workspace");
            if (Files.isDirectory(candidate) && Files.exists(candidate.resolve("limits.txt"))) {
                return candidate;
            }
        }
        throw new ToolError("cannot locate the lab workspace directory");
    }

    // ------------------------------------------------------------------ tools
    static String readFile(String relative) {
        Path workspace = workspace();
        Path target = workspace.resolve(relative).normalize();
        if (!target.startsWith(workspace)) throw new ToolError("path escapes the workspace: " + relative);
        if (!Files.exists(target)) {
            String available;
            try (var stream = Files.list(workspace)) {
                available = stream.map(p -> p.getFileName().toString()).collect(Collectors.joining(", "));
            } catch (IOException e) { available = "?"; }
            throw new ToolError("no such file: " + relative + ". Available files: " + available);
        }
        try { return Files.readString(target); }
        catch (IOException e) { throw new ToolError("could not read " + relative + ": " + e.getMessage()); }
    }

    static String httpGet(String url) {
        String host = URI.create(url).getHost();
        if (host == null || !ALLOWED_HOSTS.contains(host)) {
            throw new ToolError("host not allowed: " + host + ". Allowed: " + ALLOWED_HOSTS);
        }
        try {
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(10)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
            return response.body();
        } catch (Exception e) {
            throw new ToolError("GET " + url + " failed: " + e.getMessage());
        }
    }

    /** Arithmetic only. A tiny recursive-descent parser - no scripting engine,
     *  so there is nothing to escape into. */
    static String calculator(String expression) {
        if (!expression.matches("[0-9.+\\-*/() ]+")) {
            throw new ToolError("expression contains unsupported characters: \"" + expression
                + "\". Only numbers and + - * / ( ) are supported.");
        }
        try {
            double value = new Parser(expression).parse();
            return (value == Math.rint(value) && !Double.isInfinite(value))
                ? String.valueOf((long) value) : String.valueOf(value);
        } catch (ArithmeticException e) {
            throw new ToolError("could not evaluate \"" + expression + "\": " + e.getMessage());
        }
    }

    private static final class Parser {
        private final String s; private int i = 0;
        Parser(String s) { this.s = s.replace(" ", ""); }
        double parse() {
            double v = expr();
            if (i < s.length()) throw new ArithmeticException("unexpected '" + s.charAt(i) + "'");
            return v;
        }
        private double expr() {
            double v = term();
            while (i < s.length() && (s.charAt(i) == '+' || s.charAt(i) == '-')) {
                v = s.charAt(i++) == '+' ? v + term() : v - term();
            }
            return v;
        }
        private double term() {
            double v = factor();
            while (i < s.length() && (s.charAt(i) == '*' || s.charAt(i) == '/')) {
                if (s.charAt(i++) == '*') { v *= factor(); }
                else {
                    double d = factor();
                    if (d == 0) throw new ArithmeticException("division by zero");
                    v /= d;
                }
            }
            return v;
        }
        private double factor() {
            if (i < s.length() && s.charAt(i) == '(') {
                i++; double v = expr();
                if (i >= s.length() || s.charAt(i) != ')') throw new ArithmeticException("unbalanced parentheses");
                i++; return v;
            }
            if (i < s.length() && s.charAt(i) == '-') { i++; return -factor(); }
            int start = i;
            while (i < s.length() && (Character.isDigit(s.charAt(i)) || s.charAt(i) == '.')) i++;
            if (start == i) throw new ArithmeticException("expected a number");
            return Double.parseDouble(s.substring(start, i));
        }
    }

    // ---------------------------------------------------------------- schemas
    public static List<Map<String, Object>> schemas() {
        return List.of(
            schema("read_file",
                "Read a UTF-8 text file from the lab workspace directory. Use this to inspect "
              + "configuration and threshold files. Returns the full file contents as text.",
                "path", "File name relative to the workspace, e.g. 'limits.txt'."),
            schema("http_get",
                "Perform an HTTP GET and return the response body as text. Only the local fixture "
              + "host is reachable: http://127.0.0.1:8137/. Use this to fetch live service status.",
                "url", "Absolute URL, e.g. 'http://127.0.0.1:8137/status.json'."),
            schema("calculator",
                "Evaluate an arithmetic expression and return the numeric result. Supports + - * / "
              + "and parentheses only. Use this instead of doing arithmetic yourself.",
                "expression", "Arithmetic only, e.g. '(812 - 500) / 500'."));
    }

    private static Map<String, Object> schema(String name, String description,
                                              String param, String paramDescription) {
        return Map.of(
            "name", name,
            "description", description,
            "input_schema", Map.of(
                "type", "object",
                "properties", Map.of(param, Map.of("type", "string", "description", paramDescription)),
                "required", List.of(param)));
    }

    /** Run a tool. Returns [text, ok]. Never throws: the agent must be able to
     *  read the error and recover. */
    public static Object[] dispatch(String name, Map<String, Object> args) {
        try {
            return switch (name) {
                case "read_file"  -> new Object[]{readFile(str(args, "path")), true};
                case "http_get"   -> new Object[]{httpGet(str(args, "url")), true};
                case "calculator" -> new Object[]{calculator(str(args, "expression")), true};
                default -> new Object[]{"unknown tool: " + name
                    + ". Available: [read_file, http_get, calculator]", false};
            };
        } catch (ToolError e) {
            return new Object[]{"ERROR: " + e.getMessage(), false};
        } catch (Exception e) {
            return new Object[]{"ERROR: " + name + " failed: " + e.getMessage(), false};
        }
    }

    private static String str(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) throw new ToolError("missing required argument: " + key);
        return String.valueOf(value);
    }
}
