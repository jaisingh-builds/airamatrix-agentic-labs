package com.airamatrix.day4.lab53;

import java.math.BigDecimal;
import java.util.Iterator;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * JSON text in the format of Python's {@code json.dumps(obj, indent=2)} (ensure_ascii on), so
 * review.json from the Java reviewer reads the same as the one from review.py.
 */
final class PyJson {
    private PyJson() {}

    static String dumps(JsonNode node) {
        StringBuilder sb = new StringBuilder();
        write(node, sb, "");
        return sb.toString();
    }

    private static void write(JsonNode n, StringBuilder sb, String indent) {
        if (n == null || n.isNull() || n.isMissingNode()) {
            sb.append("null");
        } else if (n.isObject()) {
            if (n.isEmpty()) { sb.append("{}"); return; }
            String inner = indent + "  ";
            sb.append("{\n");
            Iterator<Map.Entry<String, JsonNode>> it = n.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                sb.append(inner);
                string(e.getKey(), sb);
                sb.append(": ");
                write(e.getValue(), sb, inner);
                if (it.hasNext()) sb.append(',');
                sb.append('\n');
            }
            sb.append(indent).append('}');
        } else if (n.isArray()) {
            if (n.isEmpty()) { sb.append("[]"); return; }
            String inner = indent + "  ";
            sb.append("[\n");
            for (int i = 0; i < n.size(); i++) {
                sb.append(inner);
                write(n.get(i), sb, inner);
                if (i < n.size() - 1) sb.append(',');
                sb.append('\n');
            }
            sb.append(indent).append(']');
        } else if (n.isTextual()) {
            string(n.asText(), sb);
        } else if (n.isBoolean()) {
            sb.append(n.asBoolean() ? "true" : "false");
        } else if (n.isIntegralNumber()) {
            sb.append(n.bigIntegerValue());
        } else if (n.isNumber()) {
            sb.append(pyFloat(n.asDouble()));
        } else {
            string(n.asText(), sb);
        }
    }

    /** repr(float) for the values a cost takes: 0.0, 0.0843, 1.5 */
    static String pyFloat(double d) {
        if (d == 0) return "0.0";
        String s = BigDecimal.valueOf(d).toPlainString();
        return s.contains(".") ? s : s + ".0";
    }

    private static void string(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                default -> {
                    if (c < 0x20 || c > 0x7e) sb.append(String.format("\\u%04x", (int) c));   // DEL (0x7f) too, like json.dumps
                    else sb.append(c);
                }
            }
        }
        sb.append('"');
    }
}
