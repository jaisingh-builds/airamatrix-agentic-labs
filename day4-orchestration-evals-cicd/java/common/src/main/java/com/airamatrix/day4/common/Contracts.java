package com.airamatrix.day4.common;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * The hand-off contracts between stages. The schemas are the Python lab's own
 * (exported from lab5-1-handoff/contracts.py into src/main/resources/contracts/), so a
 * proposal that passes here passes there. The validator implements the same subset of JSON
 * Schema as contracts.validate(), with the same error messages.
 */
public final class Contracts {
    public static final ObjectMapper JSON = new ObjectMapper();
    public static final JsonNode PROPOSAL = load("proposal.json");
    public static final JsonNode VERDICT = load("verdict.json");
    public static final Set<String> ALLOWED_ACTIONS = Set.of("update_config", "add_ticket_comment", "none");

    private Contracts() {}

    public static class ContractError extends RuntimeException {
        public ContractError(String message) { super(message); }
    }

    public static JsonNode load(String name) {
        try (InputStream in = Contracts.class.getResourceAsStream("/contracts/" + name)) {
            if (in == null) throw new IllegalStateException("missing contract resource " + name);
            return JSON.readTree(in);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public static JsonNode validate(JsonNode value, JsonNode schema) {
        validate(value, schema, "$");
        return value;
    }

    private static void validate(JsonNode value, JsonNode schema, String path) {
        if (schema.has("anyOf")) {
            List<String> errors = new ArrayList<>();
            for (JsonNode sub : schema.get("anyOf")) {
                try { validate(value, sub, path); return; }
                catch (ContractError e) { errors.add(e.getMessage()); }
            }
            throw new ContractError(path + ": matches none of anyOf (" + String.join("; ", errors) + ")");
        }
        if (schema.has("type")) {
            List<String> types = new ArrayList<>();
            if (schema.get("type").isArray()) schema.get("type").forEach(t -> types.add(t.asText()));
            else types.add(schema.get("type").asText());
            if (types.stream().noneMatch(t -> isType(value, t))) {
                throw new ContractError(path + ": expected " + schema.get("type") + ", got " + typeName(value));
            }
        }
        if (schema.has("enum")) {
            boolean found = false;
            for (JsonNode e : schema.get("enum")) if (e.equals(value)) found = true;
            if (!found) throw new ContractError(path + ": " + value + " is not one of " + schema.get("enum"));
        }
        if (value.isTextual()) {
            int len = value.asText().length();
            if (len < schema.path("minLength").asInt(0) || len > schema.path("maxLength").asInt(Integer.MAX_VALUE)) {
                throw new ContractError(path + ": length " + len + " outside limits");
            }
            if (schema.has("pattern") && !Pattern.compile(schema.get("pattern").asText()).matcher(value.asText()).matches()) {
                throw new ContractError(path + ": '" + value.asText() + "' does not match " + schema.get("pattern").asText());
            }
        }
        if (value.isNumber() && schema.has("minimum") && value.asDouble() < schema.get("minimum").asDouble()) {
            throw new ContractError(path + ": " + value + " below minimum");
        }
        if (value.isArray()) {
            int n = value.size();
            if (n < schema.path("minItems").asInt(0) || n > schema.path("maxItems").asInt(Integer.MAX_VALUE)) {
                throw new ContractError(path + ": " + n + " items outside limits");
            }
            JsonNode items = schema.path("items");
            for (int i = 0; i < n; i++) if (!items.isMissingNode()) validate(value.get(i), items, path + "[" + i + "]");
        }
        if (value.isObject()) {
            for (JsonNode k : schema.path("required")) {
                if (!value.has(k.asText())) throw new ContractError(path + ": missing '" + k.asText() + "'");
            }
            JsonNode props = schema.path("properties");
            if (schema.has("additionalProperties") && !schema.get("additionalProperties").asBoolean(true)) {
                Set<String> extra = new TreeSet<>();
                value.fieldNames().forEachRemaining(f -> { if (!props.has(f)) extra.add(f); });
                if (!extra.isEmpty()) throw new ContractError(path + ": unexpected " + extra);
            }
            for (Map.Entry<String, JsonNode> e : (Iterable<Map.Entry<String, JsonNode>>) value::fields) {
                if (props.has(e.getKey())) validate(e.getValue(), props.get(e.getKey()), path + "." + e.getKey());
            }
        }
    }

    private static boolean isType(JsonNode v, String t) {
        return switch (t) {
            case "object" -> v.isObject();
            case "array" -> v.isArray();
            case "string" -> v.isTextual();
            case "integer" -> v.isIntegralNumber();
            case "number" -> v.isNumber();
            case "boolean" -> v.isBoolean();
            case "null" -> v.isNull();
            default -> false;
        };
    }

    private static String typeName(JsonNode v) {
        if (v.isObject()) return "dict";
        if (v.isArray()) return "list";
        if (v.isTextual()) return "str";
        if (v.isIntegralNumber()) return "int";
        if (v.isNumber()) return "float";
        if (v.isBoolean()) return "bool";
        return "NoneType";
    }

    /** Semantic checks the schema can't express: each action's required fields. */
    public static JsonNode checkChange(JsonNode change) {
        String a = change.path("action").asText();
        List<String> need = switch (a) {
            case "update_config" -> List.of("key", "value", "expected_version");
            case "add_ticket_comment" -> List.of("ticket_id", "comment");
            case "none" -> List.of();
            default -> throw new ContractError("$.proposed_change: unknown action '" + a + "'");
        };
        List<String> missing = need.stream().filter(f -> !change.has(f)).toList();
        if (!missing.isEmpty()) throw new ContractError("$.proposed_change: " + a + " needs " + missing);
        return change;
    }

    public static ObjectNode object() { return JSON.createObjectNode(); }
}
