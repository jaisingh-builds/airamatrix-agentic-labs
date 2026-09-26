package com.airamatrix.capstone.runtime;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.core.SdkNumber;
import software.amazon.awssdk.core.document.Document;

/** JSON <-> the AWS SDK's Document (Converse tool schemas and inputs). From agentcore/06-agents-java Json. */
public final class DocJson {
    private DocJson() {}

    public static Document toDocument(Object v) {
        if (v == null) return Document.fromNull();
        if (v instanceof Document d) return d;
        if (v instanceof JsonNode n) return toDocument(Contracts.JSON.convertValue(n, Object.class));
        if (v instanceof String s) return Document.fromString(s);
        if (v instanceof Boolean b) return Document.fromBoolean(b);
        if (v instanceof Integer i) return Document.fromNumber(i);
        if (v instanceof Long l) return Document.fromNumber(l);
        if (v instanceof Double d) return Document.fromNumber(d);
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

    public static Object fromDocument(Document d) {
        if (d == null || d.isNull()) return null;
        if (d.isString()) return d.asString();
        if (d.isBoolean()) return d.asBoolean();
        if (d.isNumber()) {
            // Found live on AgentCore: the model sent 275 and it arrived as 275.0, so the contract ("integer") refused
            // a value the model never got wrong - three times. An integral number stays an integer.
            SdkNumber n = d.asNumber();
            BigDecimal bd = n.bigDecimalValue();
            if (bd.signum() == 0 || bd.stripTrailingZeros().scale() <= 0) {
                try { return bd.longValueExact(); } catch (ArithmeticException tooBig) { return bd; }
            }
            return n.doubleValue();
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

    public static JsonNode toJson(Document d) { return Contracts.JSON.valueToTree(fromDocument(d)); }
}
