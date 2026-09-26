package com.airamatrix.capstone.runtime;

import java.nio.file.Files;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.function.BiFunction;
import java.util.function.Function;
import java.util.regex.Pattern;

import com.airamatrix.capstone.OpsReader;
import com.airamatrix.capstone.Responder;
import com.airamatrix.capstone.ResponderAgent;
import com.airamatrix.capstone.Sla;
import com.airamatrix.capstone.Store;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.ModelClient;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.context.Scope;

/**
 * One invocation = one run of the SAME responder as local mode (core Responder): identity -> Gateway reads ->
 * Bedrock (guardrail on) -> code guardrail -> status. Returns the run record plus its JSONL trace records, so the
 * caller keeps one trace format for both modes. Nothing here can write: no write tool, no write token.
 */
public class InvocationService {
    static final Pattern ACCOUNT = Pattern.compile("^ACC-\\d{4}$");

    /** Opened per invocation with the Identity token; closed after. */
    public interface Reads extends OpsReader, AutoCloseable { @Override void close(); }

    private final RuntimeSettings settings;
    private final IdentityTokens identity;
    private final BiFunction<String, String, Reads> gateway;          // (gatewayUrl, bearer) -> reads
    private final Function<GenAiTelemetry, ModelClient> model;

    public InvocationService(RuntimeSettings settings, IdentityTokens identity, BiFunction<String, String, Reads> gateway,
                             Function<GenAiTelemetry, ModelClient> model) {
        this.settings = settings;
        this.identity = identity;
        this.gateway = gateway;
        this.model = model;
    }

    public ObjectNode invoke(Map<String, Object> payload, String session, String workloadToken) {
        String account = String.valueOf(payload.getOrDefault("account_id", "")), asOfText = String.valueOf(payload.getOrDefault("as_of", ""));
        if (!ACCOUNT.matcher(account).matches() || asOfText.isBlank()) throw new IllegalArgumentException("account_id and as_of are required");
        OffsetDateTime asOf = Sla.parseInstant(asOfText);
        String prompt = String.valueOf(payload.getOrDefault("prompt", ""));
        if (prompt.length() > 2000) throw new IllegalArgumentException("prompt is over 2000 characters");
        Object rid0 = payload.get("run_id");
        String rid = rid0 != null && String.valueOf(rid0).matches("^[0-9a-f]{10}$") ? String.valueOf(rid0) : Store.newId();

        GenAiTelemetry tel = new GenAiTelemetry(session);
        Spans tr = new Spans("capstone", rid);
        Span agentSpan = tel.agent(prompt);
        Responder.Outcome o;
        try (Scope ignored = agentSpan.makeCurrent()) {
            String token = identity.gatewayToken(workloadToken);
            try (Reads reads = gateway.apply(settings.gatewayUrl(), token)) {
                ResponderAgent agent = new ResponderAgent(model.apply(tel), "claude-sonnet", settings.maxTurns(), settings.maxBudgetUsd(),
                        System::getenv, tel);
                o = Responder.run(rid, account, asOf, prompt, reads, agent, tr);
            }
            agentSpan.setAttribute("gen_ai.task.output", o.status() + (o.proposal() == null ? "" : " " + o.proposal().path("summary").asText()));
        } catch (RuntimeException e) {
            GenAiTelemetry.fail(agentSpan, e);
            throw e;
        } finally {
            agentSpan.end();
        }
        ObjectNode out = o.toJson();
        out.put("mode", "agentcore").put("account_id", account).put("as_of", Sla.iso(asOf));
        ArrayNode trace = out.putArray("trace");
        try {
            for (String line : Files.readAllLines(tr.path)) if (!line.isBlank()) trace.add(Contracts.JSON.readTree(line));
        } catch (Exception e) {
            out.put("trace_error", e.getClass().getSimpleName());
        } finally {
            try { Files.deleteIfExists(tr.path); } catch (Exception ignored) { /* best effort, the container is disposable */ }
        }
        return out;
    }
}
