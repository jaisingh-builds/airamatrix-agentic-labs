package com.airamatrix.day4.lab51;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * Writes JSON exactly the way Python's {@code json.dumps} does (ensure_ascii, ", " / ": "
 * separators, indent=2 layout, float repr). Two reasons it matters here:
 * <ul>
 *   <li>{@code approvals.proposal_sha} is sha256(json.dumps(change, sort_keys=True)) - the same
 *       bytes as the Python lab, so the fingerprint is portable between the two;</li>
 *   <li>{@code show} prints the stage outputs with json.dumps(indent=2) - same text as Python.</li>
 * </ul>
 */
final class PyJson {
    private PyJson() {}

    /** json.dumps(value) */
    static String dumps(JsonNode n) { return write(n, -1, false); }

    /** json.dumps(value, indent=indent) */
    static String dumps(JsonNode n, int indent) { return write(n, indent, false); }

    /** json.dumps(value, sort_keys=True) */
    static String dumpsSorted(JsonNode n) { return write(n, -1, true); }

    private static String write(JsonNode n, int indent, boolean sort) {
        StringBuilder sb = new StringBuilder();
        write(sb, n, indent, 0, sort);
        return sb.toString();
    }

    private static void write(StringBuilder sb, JsonNode n, int indent, int level, boolean sort) {
        if (n == null || n.isNull() || n.isMissingNode()) { sb.append("null"); return; }
        if (n.isBoolean()) { sb.append(n.booleanValue() ? "true" : "false"); return; }
        if (n.isIntegralNumber()) { sb.append(n.bigIntegerValue().toString()); return; }
        if (n.isNumber()) { sb.append(pyFloat(n.doubleValue())); return; }
        if (n.isTextual()) { string(sb, n.textValue()); return; }
        if (n.isArray()) {
            if (n.isEmpty()) { sb.append("[]"); return; }
            sb.append('[');
            for (int i = 0; i < n.size(); i++) {
                if (i > 0) sb.append(indent >= 0 ? "," : ", ");
                newline(sb, indent, level + 1);
                write(sb, n.get(i), indent, level + 1, sort);
            }
            newline(sb, indent, level);
            sb.append(']');
            return;
        }
        if (n.isObject()) {
            if (n.isEmpty()) { sb.append("{}"); return; }
            List<Map.Entry<String, JsonNode>> fields = new ArrayList<>();
            for (Iterator<Map.Entry<String, JsonNode>> it = n.fields(); it.hasNext(); ) fields.add(it.next());
            if (sort) fields.sort(Map.Entry.comparingByKey());
            sb.append('{');
            for (int i = 0; i < fields.size(); i++) {
                if (i > 0) sb.append(indent >= 0 ? "," : ", ");
                newline(sb, indent, level + 1);
                string(sb, fields.get(i).getKey());
                sb.append(": ");
                write(sb, fields.get(i).getValue(), indent, level + 1, sort);
            }
            newline(sb, indent, level);
            sb.append('}');
            return;
        }
        string(sb, n.asText());
    }

    private static void newline(StringBuilder sb, int indent, int level) {
        if (indent < 0) return;
        sb.append('\n');
        sb.append(" ".repeat(indent * level));
    }

    private static void string(StringBuilder sb, String s) {
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
                    // Python's ensure_ascii escapes everything outside ' '..'~'
                    if (c < 0x20 || c > 0x7e) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
                }
            }
        }
        sb.append('"');
    }

    /** Python's repr(float): "16.0", "0.2533", "1e-05". */
    static String pyFloat(double d) {
        if (Double.isNaN(d)) return "NaN";
        if (Double.isInfinite(d)) return d > 0 ? "Infinity" : "-Infinity";
        double a = Math.abs(d);
        if (d == Math.rint(d) && a < 1e16) {
            String s = BigDecimal.valueOf(d).setScale(1, RoundingMode.UNNECESSARY).toPlainString();
            return (d == 0 && 1 / d < 0) ? "-0.0" : s;
        }
        if (a >= 1e-4 && a < 1e16) return BigDecimal.valueOf(d).stripTrailingZeros().toPlainString();
        String s = Double.toString(d);                 // e.g. 1.5E-5
        int e = s.indexOf('E');
        String mant = s.substring(0, e), exp = s.substring(e + 1);
        if (mant.endsWith(".0")) mant = mant.substring(0, mant.length() - 2);
        String sign = exp.startsWith("-") ? "-" : "+";
        String digits = exp.replace("-", "");
        if (digits.length() < 2) digits = "0" + digits;
        return mant + "e" + sign + digits;
    }

    /** round(x, 4) as Python prints it. */
    static String money(double d) {
        return pyFloat(round4(d));
    }

    static double round4(double d) {
        return BigDecimal.valueOf(d).setScale(4, RoundingMode.HALF_EVEN).doubleValue();
    }
}
