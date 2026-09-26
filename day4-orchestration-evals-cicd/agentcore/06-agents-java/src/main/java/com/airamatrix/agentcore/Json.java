package com.airamatrix.agentcore;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import software.amazon.awssdk.core.SdkNumber;
import software.amazon.awssdk.core.document.Document;

/** Conversions between plain Java/JSON values and the AWS SDK's Document (used by Converse for tool schemas and inputs). */
public final class Json {
    public static final ObjectMapper MAPPER = new ObjectMapper();
    private Json() {}

    /** Map / List / String / Number / Boolean / null -> Document. */
    public static Document toDocument(Object v) {
        if (v == null) return Document.fromNull();
        if (v instanceof Document d) return d;
        if (v instanceof JsonNode n) return toDocument(MAPPER.convertValue(n, Object.class));
        if (v instanceof String s) return Document.fromString(s);
        if (v instanceof Boolean b) return Document.fromBoolean(b);
        if (v instanceof Integer i) return Document.fromNumber(i);
        if (v instanceof Long l) return Document.fromNumber(l);
        if (v instanceof Double d) return Document.fromNumber(d);
        if (v instanceof Float f) return Document.fromNumber(f);
        if (v instanceof BigDecimal b) return Document.fromNumber(b);
        if (v instanceof Number n) return Document.fromNumber(n.toString());
        if (v instanceof Map<?, ?> m) {
            Map<String, Document> out = new LinkedHashMap<>();
            m.forEach((k, val) -> out.put(String.valueOf(k), toDocument(val)));
            return Document.fromMap(out);
        }
        if (v instanceof List<?> l) {
            List<Document> out = new ArrayList<>();
            l.forEach(x -> out.add(toDocument(x)));
            return Document.fromList(out);
        }
        return Document.fromString(String.valueOf(v));
    }

    /** Document -> Map / List / String / Number / Boolean / null. */
    public static Object fromDocument(Document d) {
        if (d == null || d.isNull()) return null;
        if (d.isString()) return d.asString();
        if (d.isBoolean()) return d.asBoolean();
        if (d.isNumber()) {
            SdkNumber n = d.asNumber();
            String s = n.stringValue();
            return s.contains(".") || s.contains("e") || s.contains("E") ? n.doubleValue() : n.longValue();
        }
        if (d.isList()) {
            List<Object> out = new ArrayList<>();
            d.asList().forEach(x -> out.add(fromDocument(x)));
            return out;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        d.asMap().forEach((k, v) -> out.put(k, fromDocument(v)));
        return out;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> mapOf(Document d) {
        Object o = fromDocument(d);
        return o instanceof Map<?, ?> m ? (Map<String, Object>) m : new LinkedHashMap<>();
    }

    public static String write(Object v) {
        try { return MAPPER.writeValueAsString(v); } catch (Exception e) { return String.valueOf(v); }
    }
}
