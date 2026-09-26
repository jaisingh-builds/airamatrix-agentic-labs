package com.airamatrix.capstone;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.GatewayAgentRunner;
import com.airamatrix.day4.common.ModelClient;
import com.airamatrix.day4.common.Spans;
import com.airamatrix.labkit.BudgetGuard;
import com.airamatrix.labkit.GatewayError;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * The single agent: a tool-use loop in plain code (Day 1's loop, grown up). Same controls as the Day 4
 * GatewayAgentRunner, for this agent's tools:
 * <ul>
 *   <li>a turn limit and a budget cap, both checked BEFORE every model call; a failure reports what it cost</li>
 *   <li>structured output: submit_proposal's schema IS the contract, validated here; one fix-up round</li>
 *   <li>refuses to start while a write/admin token is in this process (least privilege per process)</li>
 *   <li>a Bedrock Guardrail intervention (runtime mode) ends the run - it is never retried around</li>
 * </ul>
 * The model is common's {@link ModelClient}: labkit's GatewayClient in local mode, Bedrock Converse in the runtime.
 */
public final class ResponderAgent {
    public static final List<String> FORBIDDEN_ENV = GatewayAgentRunner.FORBIDDEN_ENV;
    static final int MAX_TOKENS = 3000;
    static final int CONTRACT_RETRIES = 2;       // as common's GatewayAgentRunner
    static final int MAX_RESULT_CHARS = 8000;

    public record ToolCall(String name, JsonNode input, boolean ok) {}

    public record Result(JsonNode proposal, double costUsd, int turns, List<ToolCall> toolCalls) {}

    /** A run that produced no valid proposal. kind: budget | turns | contract | no_result | gateway | guardrail_intervened | forbidden_env */
    public static final class RunError extends RuntimeException {
        public final String kind;
        public final double costUsd;
        public final int turns;
        public final List<ToolCall> toolCalls;

        RunError(String kind, String msg, double costUsd, int turns, List<ToolCall> calls) {
            super(msg);
            this.kind = kind;
            this.costUsd = costUsd;
            this.turns = turns;
            this.toolCalls = List.copyOf(calls);
        }
    }

    private final ModelClient model;
    private final String pricingModel;
    private final int maxTurns;
    private final double budgetUsd;
    private final Function<String, String> env;
    private final Telemetry telemetry;

    public ResponderAgent(ModelClient model, String pricingModel, int maxTurns, double budgetUsd,
                          Function<String, String> env, Telemetry telemetry) {
        this.model = model;
        this.pricingModel = pricingModel;
        this.maxTurns = maxTurns;
        this.budgetUsd = budgetUsd;
        this.env = env;
        this.telemetry = telemetry == null ? Telemetry.NONE : telemetry;
    }

    public Result run(String system, String prompt, Tools tools, JsonNode schema, Spans tr) {
        List<ToolCall> calls = new ArrayList<>();
        List<String> held = FORBIDDEN_ENV.stream().filter(k -> { String v = env.apply(k); return v != null && !v.isEmpty(); }).toList();
        if (!held.isEmpty()) {   // fail closed, before any cost
            throw new RunError("forbidden_env", "refusing to start the agent: " + String.join(", ", held) + " is set in this process. "
                    + "A process that runs the agent holds no write or admin token - run `apply` in its own shell.", 0, 0, calls);
        }
        List<JsonNode> toolDefs = new ArrayList<>();
        Tools.definitions(schema).forEach(toolDefs::add);
        List<Object> messages = new ArrayList<>();
        messages.add(Map.of("role", "user", "content", prompt));
        BudgetGuard budget = new BudgetGuard(budgetUsd, pricingModel);
        int contractErrors = 0, nudges = 0;
        ObjectNode previous = null;            // the last rejected submission: a fix-up is merged onto it

        for (int turn = 1; turn <= maxTurns; turn++) {
            JsonNode resp;
            try (Spans.Span sp = tr.span("model.turn", Map.of("turns", turn))) {
                try {
                    budget.check();                                   // refuse BEFORE spending
                    resp = model.messages(messages, toolDefs, system, MAX_TOKENS);
                } catch (BudgetGuard.BudgetExceeded e) {
                    sp.fail(e.getMessage());
                    throw new RunError("budget", "budget cap reached after " + (turn - 1) + " turns: " + e.getMessage(), budget.spent(), turn - 1, calls);
                } catch (GatewayError e) {
                    sp.fail(e);
                    throw new RunError("gateway", "model call failed: HTTP " + e.status + " " + Spans.redact(String.valueOf(e.getMessage())),
                            budget.spent(), turn - 1, calls);
                }
                double cost = budget.record(resp.get("usage"));
                String stop = resp.path("stop_reason").asText();
                sp.set("cost_usd", Math.round(cost * 10000) / 10000.0).set("verdict", stop);
                if ("guardrail_intervened".equals(stop)) {
                    sp.fail("Bedrock Guardrail intervened");
                    throw new RunError("guardrail_intervened", "the Bedrock Guardrail intervened on turn " + turn
                            + " - the run stops; nothing is proposed", budget.spent(), turn, calls);
                }
            }
            debugDump(turn, resp);
            JsonNode content = resp.path("content");
            messages.add(Map.of("role", "assistant", "content", content));
            if ("max_tokens".equals(resp.path("stop_reason").asText())) {
                // Found live: a reply cut off at max_tokens carried a submit_proposal with only 'summary'. A truncated
                // tool call is never validated or run - every tool_use in it gets an error result, and the model is told why.
                ArrayNode cut = Contracts.JSON.createArrayNode();
                for (JsonNode b : content) {
                    if ("tool_use".equals(b.path("type").asText())) {
                        cut.addObject().put("type", "tool_result").put("tool_use_id", b.path("id").asText()).put("is_error", true)
                                .put("content", "your reply was cut off at " + MAX_TOKENS + " tokens, so this call was not run. "
                                        + "Be brief (summary under 800 characters) and call " + Tools.SUBMIT + " again with all six keys.");
                    }
                }
                tr.event("contract.rejected", Map.of("reason", "reply cut off at max_tokens - tool calls not run"));
                if (++contractErrors > CONTRACT_RETRIES) {
                    throw new RunError("contract", "no proposal matching the contract after " + contractErrors + " attempts", budget.spent(), turn, calls);
                }
                messages.add(cut.isEmpty() ? Map.of("role", "user", "content", "Your reply was cut off. Be brief and call " + Tools.SUBMIT + ".")
                        : Map.of("role", "user", "content", cut));
                continue;
            }

            ArrayNode results = Contracts.JSON.createArrayNode();
            JsonNode submitted = null;
            for (JsonNode block : content) {
                if (!"tool_use".equals(block.path("type").asText())) continue;
                String name = block.path("name").asText();
                JsonNode input = block.path("input");
                ObjectNode r = results.addObject().put("type", "tool_result").put("tool_use_id", block.path("id").asText());
                if (name.equals(Tools.SUBMIT)) {
                    // Found live: after "missing ['evidence']" the model often resends ONLY the missing key. A fix-up takes
                    // from the previous submission ONLY the required top-level keys the new one leaves out - never a key the
                    // schema does not allow (found live in the Node build: a merge that carried everything forward kept an
                    // unexpected key alive through three correct resubmissions). Then it is validated in full - transport,
                    // not trust: the merged proposal still passes the contract and the guardrail, or nothing does.
                    JsonNode candidate = input;
                    if (previous != null && input.isObject()) {
                        ObjectNode merged = ((ObjectNode) input).deepCopy();
                        for (JsonNode k : schema.path("required")) {
                            if (!merged.has(k.asText()) && previous.has(k.asText())) merged.set(k.asText(), previous.get(k.asText()));
                        }
                        candidate = merged;
                    }
                    final JsonNode checked = candidate;
                    try {
                        Guardrails.contract(checked, schema);
                        submitted = checked;
                        r.put("content", "received");
                    } catch (Contracts.ContractError e) {
                        if (checked.isObject()) previous = (ObjectNode) checked.deepCopy();
                        contractErrors++;
                        List<String> sent = new ArrayList<>(), missing = new ArrayList<>();
                        input.fieldNames().forEachRemaining(sent::add);      // key names only - never the content
                        schema.path("required").forEach(k -> { if (!checked.has(k.asText())) missing.add(k.asText()); });
                        tr.event("contract.rejected", Map.of("reason", Tools.cut(e.getMessage(), 200), "kept", sent,
                                "dropped", Guardrails.misplaced(checked, missing)));
                        r.put("is_error", true).put("content", "contract error: " + e.getMessage() + " - call " + Tools.SUBMIT
                                + " again with the corrected keys (the keys you already sent are kept).");
                    }
                    continue;
                }
                Tools.Result res;
                try (Spans.Span ts = tr.span("tool", attrs(name, input))) {
                    res = telemetry.tool(name, input.toString(), () -> tools.call(name, input));
                    ts.set("ok", !res.error());
                    if (res.error()) ts.fail(Tools.cut(res.text(), 200));
                }
                calls.add(new ToolCall(name, input, !res.error()));
                String text = res.text().length() > MAX_RESULT_CHARS
                        ? Tools.err("too_large", "result over " + MAX_RESULT_CHARS + " chars - refused, not truncated").text() : res.text();
                r.put("content", text);
                if (res.error()) r.put("is_error", true);
            }
            if (submitted != null) return new Result(submitted, budget.spent(), turn, calls);
            if (contractErrors > CONTRACT_RETRIES) {
                throw new RunError("contract", "no proposal matching the contract after " + contractErrors + " attempts", budget.spent(), turn, calls);
            }
            if (!results.isEmpty()) {
                messages.add(Map.of("role", "user", "content", results));
                continue;
            }
            if (++nudges > 1) {
                throw new RunError("no_result", "the agent answered in text instead of calling " + Tools.SUBMIT, budget.spent(), turn, calls);
            }
            messages.add(Map.of("role", "user", "content", "Call " + Tools.SUBMIT + " now with your proposal."));
        }
        throw new RunError("turns", "turn limit " + maxTurns + " reached without a proposal", budget.spent(), maxTurns, calls);
    }

    /** CAPSTONE_DEBUG_DIR: raw model replies, one file per turn, for when the trace is not enough. Holds customer text:
     *  local only, never committed, delete it when done. Off by default. */
    private void debugDump(int turn, JsonNode resp) {
        String dir = env.apply("CAPSTONE_DEBUG_DIR");
        if (dir == null || dir.isBlank()) return;
        try {
            java.nio.file.Path d = java.nio.file.Paths.get(dir);
            java.nio.file.Files.createDirectories(d);
            java.nio.file.Files.writeString(d.resolve(System.currentTimeMillis() + "-turn" + turn + ".json"),
                    Spans.redact(resp.toString()));
        } catch (Exception ignored) { /* debugging aid only */ }
    }

    static Map<String, Object> attrs(String tool, JsonNode input) {
        Map<String, Object> a = new LinkedHashMap<>();
        a.put("tool", tool);
        a.put("input", Contracts.JSON.convertValue(input, Map.class));
        return a;
    }
}
