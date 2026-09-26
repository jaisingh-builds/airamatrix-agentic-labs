package com.airamatrix.capstone;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * One run, the same in both modes: agent -> guardrail (against the SLA recomputed from source) -> a status
 * that says whether a human has something to decide. Used by the local CLI, the eval harness and the
 * AgentCore runtime's /invocations.
 *
 *   failed | guardrail_intervened   the agent produced no proposal (budget, turns, contract, Bedrock Guardrail)
 *   blocked                         the code guardrail refused the proposal - a human cannot approve it
 *   no_action                       nothing to post (nothing exposed, or no exposed ticket)
 *   awaiting_approval               a customer update is waiting for a named human with a reason
 */
public final class Responder {
    private Responder() {}

    public record Outcome(String runId, String status, JsonNode proposal, Guardrails.Verdict verdict, Sla.Report sla,
                          List<ResponderAgent.ToolCall> toolCalls, double costUsd, int turns, String error) {

        public ArrayNode trajectory() {
            ArrayNode a = Contracts.JSON.createArrayNode();
            for (ResponderAgent.ToolCall c : toolCalls) a.addArray().add(c.name()).add(c.input()).add(c.ok());
            return a;
        }

        public ObjectNode toJson() {
            ObjectNode o = Contracts.object().put("run_id", runId).put("status", status)
                    .put("cost_usd", Math.round(costUsd * 10000) / 10000.0).put("turns", turns).put("tool_calls", toolCalls.size());
            if (proposal != null) o.set("proposal", proposal);
            if (verdict != null) o.set("verdict", verdict.toJson());
            if (sla != null) o.set("sla", sla.toJson());
            o.set("trajectory", trajectory());
            if (error != null) o.put("error", error);
            return o;
        }
    }

    public static Outcome run(String runId, String accountId, OffsetDateTime asOf, String question,
                              OpsReader ops, ResponderAgent agent, Spans tr) {
        try (Spans.Span root = tr.span("run", Map.of("account", accountId, "stage", "sla-responder"))) {
            ResponderAgent.Result r;
            try {
                r = agent.run(Prompts.SYSTEM, Prompts.task(accountId, Sla.iso(asOf), question), new Tools(ops, accountId, asOf),
                        Guardrails.SCHEMA, tr);
            } catch (ResponderAgent.RunError e) {
                String status = e.kind.equals("guardrail_intervened") ? "guardrail_intervened" : "failed";
                root.set("cost_usd", round(e.costUsd)).set("turns", e.turns).set("verdict", status);
                root.fail(e.kind + ": " + e.getMessage());
                return new Outcome(runId, status, null, null, null, e.toolCalls, e.costUsd, e.turns, e.kind + ": " + e.getMessage());
            }
            root.set("cost_usd", round(r.costUsd())).set("turns", r.turns()).set("tool_calls", r.toolCalls().size());

            Sla.Report sla;
            Guardrails.Verdict v;
            try (Spans.Span vs = tr.span("guardrail.verify", Map.of("stage", "code-guardrail"))) {
                try {
                    sla = Sla.compute(ops, accountId, asOf);        // source of truth, recomputed - not what the agent saw
                } catch (RuntimeException e) {                      // cannot verify -> fail closed
                    vs.fail(e);
                    root.fail("verification could not run: " + e.getMessage());
                    return new Outcome(runId, "failed", r.proposal(), null, null, r.toolCalls(), r.costUsd(), r.turns(),
                            "verification could not run: " + e.getMessage());
                }
                v = Guardrails.verify(r.proposal(), sla);
                vs.set("verdict", v.passed() ? "pass" : "block").set("denials", v.rules()).set("kept", sla.exposed().size());
                if (!v.passed()) vs.fail("guardrail refused: " + String.join(", ", v.rules()));
            }
            String action = r.proposal().path("action").path("type").asText();
            String status = !v.passed() ? "blocked" : action.equals("none") ? "no_action" : "awaiting_approval";
            root.set("verdict", status).set("action", action);
            if (status.equals("awaiting_approval")) {
                tr.event("gate.waiting", Map.of("action", action, "input", Map.of("ticket_id", r.proposal().path("action").path("ticket_id").asText())));
            }
            return new Outcome(runId, status, r.proposal(), v, sla, r.toolCalls(), r.costUsd(), r.turns(), null);
        }
    }

    /** Persist an outcome (local runs, AgentCore invocations, replays). */
    public static void save(Store store, Outcome o, String trace) {
        store.finishRun(o.runId(), o.status(), o.costUsd(), o.turns(), o.toolCalls().size(), o.error(), trace);
        if (o.proposal() != null) {
            store.saveProposal(o.runId(), o.proposal(), o.sla() == null ? null : o.sla().toJson(),
                    o.verdict() == null ? null : o.verdict().toJson(), o.trajectory());
        }
    }

    static double round(double d) { return Math.round(d * 10000) / 10000.0; }
}
