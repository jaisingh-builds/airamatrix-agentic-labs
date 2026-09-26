package com.airamatrix.day4.lab52;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * Python-compatible text for the few places where the exact string matters: the graders search
 * {@code json.dumps(...)} text with regexes, and the failure details / report must read the same
 * as the Python lab's (so a result is comparable across the two harnesses).
 */
final class Py {
    private Py() {}

    /** json.dumps(v) with Python's defaults: ", " and ": " separators, ensure_ascii=True. */
    static String dumps(JsonNode v) {
        StringBuilder sb = new StringBuilder();
        dumps(v, sb);
        return sb.toString();
    }

    private static void dumps(JsonNode v, StringBuilder sb) {
        if (v == null || v.isMissingNode() || v.isNull()) { sb.append("null"); return; }
        if (v.isObject()) {
            sb.append('{');
            Iterator<Map.Entry<String, JsonNode>> it = v.fields();
            boolean first = true;
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                if (!first) sb.append(", ");
                first = false;
                quote(e.getKey(), sb);
                sb.append(": ");
                dumps(e.getValue(), sb);
            }
            sb.append('}');
        } else if (v.isArray()) {
            sb.append('[');
            for (int i = 0; i < v.size(); i++) {
                if (i > 0) sb.append(", ");
                dumps(v.get(i), sb);
            }
            sb.append(']');
        } else if (v.isTextual()) {
            quote(v.asText(), sb);
        } else if (v.isBoolean()) {
            sb.append(v.asBoolean() ? "true" : "false");
        } else if (v.isIntegralNumber()) {
            sb.append(v.bigIntegerValue());
        } else if (v.isNumber()) {
            double d = v.asDouble();
            sb.append(Double.isNaN(d) ? "NaN" : Double.isInfinite(d) ? (d > 0 ? "Infinity" : "-Infinity") : floatRepr(d));
        } else {
            quote(v.asText(), sb);
        }
    }

    private static void quote(String s, StringBuilder sb) {
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
                    if (c < 0x20 || c > 0x7e) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
                }
            }
        }
        sb.append('"');
    }

    /** Python repr(float). */
    static String floatRepr(double d) {
        if (d == 0) return (1 / d < 0) ? "-0.0" : "0.0";
        double a = Math.abs(d);
        String s = Double.toString(d);          // shortest round-trip digits, like Python
        if (a >= 1e-4 && a < 1e16) {
            String plain = new BigDecimal(s).toPlainString();
            return plain.contains(".") ? plain : plain + ".0";
        }
        int e = s.indexOf('E');
        String mant = s.substring(0, e), exp = s.substring(e + 1);
        if (mant.endsWith(".0")) mant = mant.substring(0, mant.length() - 2);
        int x = Integer.parseInt(exp);
        return mant + "e" + (x < 0 ? "-" : "+") + String.format("%02d", Math.abs(x));
    }

    /** str(value) of a Python value parsed from JSON: None / True / 8 / 8.0 / text. */
    static String str(JsonNode v) {
        if (v == null || v.isMissingNode() || v.isNull()) return "None";
        if (v.isTextual()) return v.asText();
        if (v.isBoolean()) return v.asBoolean() ? "True" : "False";
        if (v.isIntegralNumber()) return v.bigIntegerValue().toString();
        if (v.isNumber()) return floatRepr(v.asDouble());
        return repr(v);
    }

    /** repr(value): strings quoted the way Python quotes them. */
    static String repr(JsonNode v) {
        if (v == null || v.isMissingNode() || v.isNull()) return "None";
        if (v.isTextual()) return repr(v.asText());
        if (v.isArray()) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < v.size(); i++) sb.append(i > 0 ? ", " : "").append(repr(v.get(i)));
            return sb.append(']').toString();
        }
        if (v.isObject()) {
            StringBuilder sb = new StringBuilder("{");
            Iterator<Map.Entry<String, JsonNode>> it = v.fields();
            boolean first = true;
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                sb.append(first ? "" : ", ").append(repr(e.getKey())).append(": ").append(repr(e.getValue()));
                first = false;
            }
            return sb.append('}').toString();
        }
        return str(v);
    }

    static String repr(String s) {
        char q = (s.indexOf('\'') >= 0 && s.indexOf('"') < 0) ? '"' : '\'';
        StringBuilder sb = new StringBuilder().append(q);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\') sb.append("\\\\");
            else if (c == q) sb.append('\\').append(c);
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else if (c < 0x20 || c == 0x7f) sb.append(String.format("\\x%02x", (int) c));
            else sb.append(c);
        }
        return sb.append(q).toString();
    }

    /** repr(list_of_str), e.g. ['run2-claim-in-prompt'] or []. */
    static String repr(List<String> items) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < items.size(); i++) sb.append(i > 0 ? ", " : "").append(repr(items.get(i)));
        return sb.append(']').toString();
    }

    /** round(x, n) - exact binary value, half-even, like Python. */
    static double round(double x, int n) {
        return new BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).doubleValue();
    }

    /** f"{x:.nf}". */
    static String fixed(double x, int n) {
        return new BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).toPlainString();
    }

    /** f"{x:.0%}". */
    static String pct(double x) {
        return fixed(x * 100, 0) + "%";
    }

    /** str(round(x, 1)) for seconds, e.g. 12.3 / 0.0. */
    static String seconds(double x) {
        return floatRepr(round(x, 1));
    }

    /** s[:n]. */
    static String head(String s, int n) {
        return s == null ? "" : (s.length() <= n ? s : s.substring(0, n));
    }

    /** f"{s:<n}". */
    static String left(String s, int n) {
        return s.length() >= n ? s : s + " ".repeat(n - s.length());
    }
}
