package com.airamatrix.capstone;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * THE GUARDRAIL - in code, not in the prompt. A proposal reaches a human only if every rule passes;
 * a blocked proposal cannot be approved, even with an override (a guardrail a human can click past is a
 * warning). The same outbound rules run again inside {@link Gate#apply}, at the point of the write.
 *
 * <pre>
 * rule                    refuses
 * contract.*              output that does not match sla-proposal.json, or an action missing its fields
 * claims.unknown_item     an "exposed" item sla_report does not have (invented)
 * claims.wrong_state      at_risk vs breached wrong
 * claims.wrong_numbers    elapsed/target minutes not what the code computed (+-2 min)
 * claims.omitted          an at_risk/breached item left out (hiding exposure from the duty manager)
 * action.out_of_scope     a ticket that is not this account's (another tenant, or does not exist)
 * action.not_exposed      a customer update on a ticket that is not at_risk/breached
 * comment.length          under 40 or over 700 characters
 * comment.secret          anything secret-shaped: bearer/sk-/hex tokens, a secret env value, AIRA_OPS_* names
 * comment.internal_config an internal setting name (ingest.*, alerts.*, viewer.*, feature.*)
 * comment.other_tenant    another account's id (ACC-nnnn)
 * comment.foreign_id      a ticket or job id that is not this account's
 * comment.link            a URL - customer updates carry no links (a classic exfiltration channel)
 * </pre>
 */
public final class Guardrails {
    private Guardrails() {}

    public static final JsonNode SCHEMA = Contracts.load("sla-proposal.json");
    static final int TOLERANCE_MIN = 2;
    static final Pattern CONFIG_NAME = Pattern.compile("\\b(ingest|alerts|viewer|feature)\\.[a-z_]+", Pattern.CASE_INSENSITIVE);
    static final Pattern ACCOUNT_ID = Pattern.compile("\\bACC-\\d{4}\\b");
    static final Pattern OBJECT_ID = Pattern.compile("\\b[TJ]-\\d{4}\\b");
    static final Pattern LINK = Pattern.compile("(?i)\\b(https?://|www\\.)");
    static final Pattern SECRET_WORDS = Pattern.compile("(?i)\\b(AIRA_OPS_[A-Z_]+|ANTHROPIC_[A-Z_]+|bearer\\s+\\S{8,})");

    public record Denial(String rule, String detail) {
        public String id() { return rule; }
    }

    public record Verdict(boolean passed, List<Denial> denials) {
        public ObjectNode toJson() {
            ObjectNode o = Contracts.object().put("passed", passed);
            ArrayNode a = o.putArray("denials");
            denials.forEach(d -> a.addObject().put("rule", d.rule()).put("detail", d.detail()));
            return o;
        }

        public List<String> rules() { return denials.stream().map(Denial::rule).distinct().toList(); }

        public static Verdict fromJson(JsonNode n) {
            List<Denial> d = new ArrayList<>();
            n.path("denials").forEach(x -> d.add(new Denial(x.path("rule").asText(), x.path("detail").asText())));
            return new Verdict(n.path("passed").asBoolean(false), d);
        }
    }

    /** Schema + per-action required fields. Throws ContractError - the agent loop sends it back for one fix-up. */
    public static JsonNode contract(JsonNode p, JsonNode schema) {
        // Top level first, all at once: "missing 'exposed'" alone did not tell the model it had sent 'exposed_items'
        if (p.isObject()) {
            List<String> missing = new ArrayList<>(), extra = new ArrayList<>();
            schema.path("required").forEach(k -> { if (!p.has(k.asText())) missing.add(k.asText()); });
            p.fieldNames().forEachRemaining(k -> { if (!schema.path("properties").has(k)) extra.add(k); });
            if (!missing.isEmpty() || !extra.isEmpty()) {
                List<String> misplaced = misplaced(p, missing);
                throw new Contracts.ContractError("$: " + (missing.isEmpty() ? "" : "missing " + pyList(missing))
                        + (!missing.isEmpty() && !extra.isEmpty() ? "; " : "") + (extra.isEmpty() ? "" : "unexpected " + pyList(extra))
                        + (misplaced.isEmpty() ? "" : " (found at " + String.join(", ", misplaced) + " - move it to the top level)")
                        + " - the top-level keys are exactly " + pyList(keys(schema.path("required"))));
            }
        }
        Contracts.validate(p, schema);
        JsonNode a = p.path("action");
        if (a.path("type").asText().equals("post_customer_update")) {
            if (!a.hasNonNull("ticket_id") || !a.hasNonNull("comment")) {
                throw new Contracts.ContractError("$.action: post_customer_update needs ticket_id and comment");
            }
        } else if (a.has("comment") || a.has("ticket_id")) {
            throw new Contracts.ContractError("$.action: action none takes no ticket_id or comment");
        }
        return p;
    }

    /** Where a missing top-level key was put instead, e.g. $.action.evidence - key paths only, never values. */
    public static List<String> misplaced(JsonNode p, List<String> missing) {
        List<String> out = new ArrayList<>();
        walk(p, "$", missing, out, 0);
        return out;
    }

    private static void walk(JsonNode n, String path, List<String> names, List<String> out, int depth) {
        if (depth > 4) return;
        if (n.isObject()) {
            n.fields().forEachRemaining(f -> {
                String here = path + "." + f.getKey();
                if (depth > 0 && names.contains(f.getKey())) out.add(here);
                walk(f.getValue(), here, names, out, depth + 1);
            });
        } else if (n.isArray()) {
            for (int i = 0; i < n.size(); i++) walk(n.get(i), path + "[" + i + "]", names, out, depth + 1);
        }
    }

    /** ['a', 'b'] - the same text in Java, Python and Node. */
    public static String pyList(List<String> xs) {
        return "[" + String.join(", ", xs.stream().map(x -> "'" + x + "'").toList()) + "]";
    }

    static List<String> keys(JsonNode arr) {
        List<String> out = new ArrayList<>();
        arr.forEach(x -> out.add(x.asText()));
        return out;
    }

    /** Every rule, against the SLA recomputed from source - not against what the agent says it saw. */
    public static Verdict verify(JsonNode p, Sla.Report sla) {
        List<Denial> out = new ArrayList<>();
        try {
            contract(p, SCHEMA);
        } catch (Contracts.ContractError e) {
            out.add(new Denial("contract.invalid", Tools.cut(e.getMessage(), 300)));
            return new Verdict(false, out);
        }
        Set<String> claimed = new HashSet<>();
        for (JsonNode c : p.path("exposed")) {
            String id = c.path("item").asText();
            if (!claimed.add(id)) { out.add(new Denial("claims.duplicate", id + " listed twice")); continue; }
            Sla.Item it = sla.item(id);
            if (it == null || it.state().equals("ok")) {
                out.add(new Denial("claims.unknown_item", id + " is not at_risk/breached in sla_report"
                        + (it == null ? "" : " (it is ok, " + it.pct() + "% of target)")));
                continue;
            }
            if (!it.state().equals(c.path("state").asText())) {
                out.add(new Denial("claims.wrong_state", id + ": claimed " + c.path("state").asText() + ", actually " + it.state()));
            }
            long el = c.path("elapsed_minutes").asLong(), tg = c.path("target_minutes").asLong();
            if (Math.abs(el - it.elapsedMinutes()) > TOLERANCE_MIN || tg != it.targetMinutes()) {
                out.add(new Denial("claims.wrong_numbers", id + ": claimed " + el + "/" + tg + " min, computed "
                        + it.elapsedMinutes() + "/" + it.targetMinutes()));
            }
        }
        for (Sla.Item it : sla.exposed()) {
            if (!claimed.contains(it.id())) out.add(new Denial("claims.omitted", it.id() + " is " + it.state() + " but not listed"));
        }
        JsonNode a = p.path("action");
        if (a.path("type").asText().equals("post_customer_update")) {
            String tid = a.path("ticket_id").asText();
            Sla.Item it = sla.item(tid);
            if (!sla.ticketIds().contains(tid)) {
                out.add(new Denial("action.out_of_scope", tid + " is not a ticket of " + sla.accountId()));
            } else if (it == null || it.state().equals("ok")) {
                out.add(new Denial("action.not_exposed", tid + " is " + (it == null ? "not tracked or not open" : "ok (" + it.pct() + "% of target)")
                        + " - a customer update needs an at_risk or breached ticket"));
            }
            out.addAll(outbound(a.path("comment").asText(), sla));
        }
        return new Verdict(out.isEmpty(), out);
    }

    /** What may leave the building in a customer-visible comment. Also run by Gate.apply before the write. */
    public static List<Denial> outbound(String comment, Sla.Report sla) {
        List<Denial> out = new ArrayList<>();
        String c = comment == null ? "" : comment;
        if (c.strip().length() < 40 || c.length() > 700) out.add(new Denial("comment.length", c.length() + " chars (40-700)"));
        if (!Spans.redact(c).equals(c) || SECRET_WORDS.matcher(c).find()) {
            out.add(new Denial("comment.secret", "secret-shaped text in a customer-visible comment"));
        }
        Matcher m = CONFIG_NAME.matcher(c);
        if (m.find()) out.add(new Denial("comment.internal_config", "internal setting '" + m.group() + "' in a customer update"));
        m = ACCOUNT_ID.matcher(c);
        while (m.find()) {
            if (!m.group().equals(sla.accountId())) { out.add(new Denial("comment.other_tenant", m.group() + " is another customer")); break; }
        }
        m = OBJECT_ID.matcher(c);
        while (m.find()) {
            if (!sla.inScope(m.group())) { out.add(new Denial("comment.foreign_id", m.group() + " is not " + sla.accountId() + "'s")); break; }
        }
        if (LINK.matcher(c).find()) out.add(new Denial("comment.link", "customer updates carry no links"));
        return out;
    }
}
