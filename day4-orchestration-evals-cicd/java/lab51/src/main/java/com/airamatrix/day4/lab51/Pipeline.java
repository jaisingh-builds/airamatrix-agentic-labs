package com.airamatrix.day4.lab51;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;

import com.airamatrix.day4.common.AgentRunner;
import com.airamatrix.day4.common.AgentRunner.AgentResult;
import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * Lab 5.1 - a two-stage agent pipeline with a shared state store and a human gate.
 * Port of lab5-1-handoff/pipeline.py (the stages, the gate, the only write, resume, replay).
 *
 * <pre>
 *     investigate (agent) -> review (agent) -> HUMAN GATE -> apply (plain code)
 *             \______________ shared state store: runs.sqlite ______________/
 * </pre>
 *
 * Credentials, least privilege:
 *   AIRA_OPS_READ_TOKEN   the agents' token: read-only, scoped to the account
 *   AIRA_OPS_APPLY_TOKEN  the apply step's token: may write. No agent ever sees it.
 */
public class Pipeline {

    public static final String DEFAULT_OPS_URL = "http://127.0.0.1:8150";

    /** Raised when something tries to cross the human gate without a human. */
    public static class GateError extends RuntimeException {
        public GateError(String message) { super(message); }
    }

    private final Store store;
    /** APPLY_TIMEOUT_S: how long apply waits for aira-ops before calling the outcome unknown. */
    public Duration applyTimeout;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();

    public Pipeline(Store store) {
        this(store, Duration.ofMillis((long) (Double.parseDouble(envOr("APPLY_TIMEOUT_S", "8")) * 1000)));
    }

    public Pipeline(Store store, Duration applyTimeout) {
        this.store = store;
        this.applyTimeout = applyTimeout;
    }

    public Store store() { return store; }

    static String envOr(String name, String fallback) {
        String v = System.getenv(name);
        return v == null || v.isEmpty() ? fallback : v;
    }

    public static String opsUrl(java.util.function.Function<String, String> env) {
        String v = env.apply("AIRA_OPS_URL");
        return (v == null || v.isEmpty() ? DEFAULT_OPS_URL : v).replaceAll("/+$", "");
    }

    // --------------------------------------------------------------- the stages
    /** One agent stage, checkpointed: a finished stage is never run (or paid for) twice. */
    JsonNode runStage(AgentRunner runner, Spans tracer, String rid, String name, String system, String prompt,
                      JsonNode schema, Consumer<JsonNode> extraCheck) {
        // >>> TODO 1: checkpoint - a finished stage is never run (or paid for) twice
        Store.StageRow done = store.stage(rid, name);
        if (done != null && "done".equals(done.status())) {
            tracer.event("stage." + name + ".skipped", Map.of("reason", "checkpoint: already done"));
            return done.output();
        }
        // <<< TODO 1
        int attempt = store.stageStarted(rid, name);
        try (Spans.Span sp = tracer.span("stage." + name, Map.of("attempt", attempt))) {
            AgentResult res = null;
            try {
                res = runner.run(name, system, prompt, schema);
                if (res.output() == null) throw new Contracts.ContractError("$: expected object, got NoneType");
                Contracts.validate(res.output(), schema);          // the contract, enforced - not hoped for
                if (extraCheck != null) extraCheck.accept(res.output());
            } catch (RuntimeException e) {
                double cost = (e instanceof RunnerException re && re.costUsd != 0) ? re.costUsd
                        : (res != null ? res.costUsd() : 0.0);
                sp.set("cost_usd", PyJson.round4(cost));
                store.stageFailed(rid, name, describe(e), cost);
                store.setStatus(rid, name + "_failed");
                sp.fail(describe(e));
                throw e;
            }
            for (int i = 0; i < res.toolCalls().size(); i++) {
                AgentRunner.ToolCall call = res.toolCalls().get(i);
                Map<String, Object> attrs = new LinkedHashMap<>();
                attrs.put("tool", call.name());
                attrs.put("input", toMap(call.input()));
                attrs.put("ok", i < res.toolOk().size() ? res.toolOk().get(i) : null);
                tracer.event("tool_call", attrs);
            }
            sp.set("cost_usd", PyJson.round4(res.costUsd())).set("tool_calls", res.toolCalls().size()).set("turns", res.turns());
            store.stageDone(rid, name, res.output(), res.costUsd(), res.toolCalls().size());
            return res.output();
        }
    }

    /** Run every unfinished agent stage, then stop at the gate. */
    public Store.RunRow advance(AgentRunner runner, String rid) { return advance(runner, rid, null); }

    public Store.RunRow advance(AgentRunner runner, String rid, Spans tracer) {
        Store.RunRow r = store.run(rid);
        Spans tr = tracer != null ? tracer : new Spans("lab5-1", rid);
        try (Spans.Span sp = tr.span("pipeline.advance", Map.of("account", String.valueOf(r.accountId())))) {
            try {
                JsonNode proposal = runStage(runner, tr, rid, "investigate", Agents.INVESTIGATE_SYSTEM,
                        Agents.investigatePrompt(r.accountId(), r.question()), Contracts.PROPOSAL,
                        o -> Contracts.checkChange(o.get("proposed_change")));
                store.setStatus(rid, "investigated");
                if ("none".equals(proposal.path("proposed_change").path("action").asText())) {
                    store.setStatus(rid, "no_change");
                    return store.run(rid);
                }
                JsonNode verdict = runStage(runner, tr, rid, "review", Agents.REVIEW_SYSTEM,
                        Agents.reviewPrompt(r.accountId(), r.question(), proposal), Contracts.VERDICT, null);
                store.setStatus(rid, "reviewed");
                // Only an APPROVE verdict waits for a plain approval. BLOCK and REVISE both mean "not this change":
                // approving the original proposal anyway needs an explicit override and a reason.
                String v = verdict.path("verdict").asText();
                store.setStatus(rid, "approve".equals(v) ? "awaiting_approval" : "needs_rework");
                tr.event("gate.waiting", Map.of("verdict", v));
            } catch (RuntimeException e) {
                sp.fail(describe(e));
                throw e;
            }
        }
        return store.run(rid);
    }

    // --------------------------------------------------------------- the gate
    /** A human decision, recorded with a name and a reason. Nothing else opens the gate. */
    public Store.RunRow decide(String rid, String decision, String approver, String reason, boolean override) {
        if (!"approve".equals(decision) && !"reject".equals(decision)) {
            throw new IllegalArgumentException("decision must be approve or reject");
        }
        // >>> TODO 2: the gate - who may decide, when, and what gets recorded
        if (approver == null || approver.isBlank() || reason == null || reason.isBlank()) {
            throw new GateError("a decision needs an approver name and a reason");
        }
        Store.RunRow r = store.run(rid);
        if (store.approval(rid) != null) {
            throw new GateError("run " + rid + " was already decided");
        }
        if ("needs_rework".equals(r.status()) && "approve".equals(decision) && !override) {
            Store.StageRow review = store.stage(rid, "review");
            String v = review == null || review.output() == null ? null : review.output().path("verdict").asText(null);
            String what = "block".equals(v) ? "blocked this proposal" : "asked for a safer change than this proposal";
            throw new GateError("the reviewer " + what + "; approving it needs --override and a reason");
        }
        if (!List.of("awaiting_approval", "needs_rework").contains(r.status())) {
            throw new GateError("run " + rid + " is " + r.status() + ", not waiting for a decision");
        }
        store.recordDecision(rid, decision, approver.strip(), reason.strip(), override);
        store.setStatus(rid, "approve".equals(decision) ? "approved" : "rejected");
        // <<< TODO 2
        Map<String, Object> attrs = new LinkedHashMap<>();
        attrs.put("decision", decision);
        attrs.put("approver", approver);
        attrs.put("override", override);
        new Spans("lab5-1", rid).event("gate.decided", attrs);
        return store.run(rid);
    }

    // --------------------------------------------------------------- the only write
    /** Make the approved change. Plain code, not an agent. Safe to call again. */
    public Store.RunRow apply(String rid, String writeToken, String opsUrl) { return apply(rid, writeToken, opsUrl, null); }

    public Store.RunRow apply(String rid, String writeToken, String opsUrl, Spans tracer) {
        Spans tr = tracer != null ? tracer : new Spans("lab5-1", rid);
        Store.RunRow r = store.run(rid);
        // >>> TODO 3: no approval on record, no write
        Store.ApprovalRow a = store.approval(rid);
        // The gate is checked against the DECISION RECORD, not just the status field.
        if (a == null || !"approve".equals(a.decision())) {
            throw new GateError("run " + rid + " has no approval on record");
        }
        // <<< TODO 3
        if (a.proposalSha() == null || !a.proposalSha().equals(store.proposalSha(rid))) {
            throw new GateError("run " + rid + ": the proposal changed after it was decided - it needs a new decision");
        }
        if ("applied".equals(r.status())) {
            return r;                    // already done: applying again is a no-op, not an error
        }
        if (!List.of("approved", "outcome_unknown").contains(r.status())) {
            throw new GateError("run " + rid + " is " + r.status() + "; only an approved run can be applied");
        }
        JsonNode change = Contracts.checkChange(store.stage(rid, "investigate").output().get("proposed_change").deepCopy());
        String action = change.path("action").asText();
        if (!Contracts.ALLOWED_ACTIONS.contains(action) || "none".equals(action)) {
            throw new GateError("action '" + action + "' is not something this pipeline may do");
        }

        // Retry state belongs to the orchestrator: the operation id is stored BEFORE the
        // request is sent, so a crash or timeout followed by `apply` again sends the SAME
        // id and aira-ops applies the change at most once.
        // >>> TODO 4: the operation id is stored BEFORE the request is sent
        Store.OperationRow op = store.operation(rid);
        if (op == null) {
            store.recordOperation(rid, UUID.randomUUID().toString(), action, change);
            op = store.operation(rid);
        }
        // <<< TODO 4
        if ("done".equals(op.status())) {
            return store.run(rid);
        }
        Request req = requestFor(change);
        Map<String, Object> attrs = new LinkedHashMap<>();
        attrs.put("action", action);
        attrs.put("op_id", op.opId());
        attrs.put("approver", a.approver());
        try (Spans.Span sp = tr.span("apply", attrs)) {
            HttpResult res = http(req.method(), opsUrl + req.path(), req.body(), writeToken, op.opId());
            sp.set("http_status", res.status()).set("replayed", res.body().path("_replayed").asBoolean(false));
            if (res.status() == 200 || res.status() == 201) {
                store.operationResult(rid, "done", res.body());
                store.setStatus(rid, "applied");
            } else if (res.status() == 0 || res.status() >= 500) {
                store.operationResult(rid, "pending", res.body());
                store.setStatus(rid, "outcome_unknown");
                sp.fail("outcome unknown - run `apply` again; the same operation id makes it safe");
            } else {
                store.operationResult(rid, "failed", res.body());
                store.setStatus(rid, "apply_failed");
                sp.fail("HTTP " + res.status());
            }
        }
        return store.run(rid);
    }

    public record Request(String method, String path, JsonNode body) {}

    public record HttpResult(int status, JsonNode body) {}

    /** The one mapping from an approved change to an aira-ops write. Same as pipeline.request_for. */
    public static Request requestFor(JsonNode change) {
        ObjectNode body = Contracts.object();
        if ("update_config".equals(change.path("action").asText())) {
            body.set("value", change.get("value"));
            body.set("expected_version", change.get("expected_version"));
            return new Request("PUT", "/config/" + change.get("key").asText(), body);
        }
        body.set("body", change.get("comment"));
        return new Request("POST", "/tickets/" + change.get("ticket_id").asText() + "/comments", body);
    }

    HttpResult http(String method, String url, JsonNode body, String token, String opId) {
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .timeout(applyTimeout)
                    .header("Authorization", "Bearer " + token)
                    .header("Content-Type", "application/json")
                    .header("Idempotency-Key", opId)
                    .method(method, HttpRequest.BodyPublishers.ofString(PyJson.dumps(body), StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            return new HttpResult(resp.statusCode(), parseBody(resp.body()));
        } catch (IOException | IllegalArgumentException e) {        // timeout, refused, reset: the outcome is unknown
            ObjectNode err = Contracts.object();
            err.putObject("error").put("code", "unavailable").put("message", String.valueOf(e.getMessage() != null
                    ? e.getMessage() : e.getClass().getSimpleName()));
            return new HttpResult(0, err);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            ObjectNode err = Contracts.object();
            err.putObject("error").put("code", "unavailable").put("message", "interrupted");
            return new HttpResult(0, err);
        }
    }

    private static JsonNode parseBody(String s) {
        if (s == null || s.isBlank()) return Contracts.object();
        try {
            return Contracts.JSON.readTree(s);
        } catch (IOException e) {
            ObjectNode o = Contracts.object();
            o.put("raw", s.length() > 500 ? s.substring(0, 500) : s);
            return o;
        }
    }

    /**
     * Finish the agent stages of a run that stopped. Never writes: an approved run, or one whose
     * write has an unknown outcome, is finished with `apply` - a separate process holding the write token.
     */
    public Store.RunRow resume(AgentRunner runner, String rid) {
        Store.RunRow r = store.run(rid);
        String s = r.status();
        if (List.of("created", "investigated", "reviewed").contains(s) || (s.endsWith("_failed") && !s.equals("apply_failed"))) {
            return advance(runner, rid);
        }
        return r;   // terminal, waiting at the gate, or waiting for `apply`
    }

    // --------------------------------------------------------------- replay ($0)
    /** Plays saved stage outputs back through the real stages, contracts and gate. No model, no cost. */
    public static class ReplayRunner implements AgentRunner {
        private final JsonNode stages;
        public ReplayRunner(JsonNode fixture) { this.stages = fixture.get("stages"); }
        @Override
        public AgentResult run(String stage, String system, String prompt, JsonNode schema) {
            JsonNode saved = stages.get(stage);
            return new AgentResult(saved.get("output").deepCopy(), 0.0, List.of(), 0);
        }
    }

    /** A real, saved run - e.g. one the reviewer BLOCKED - so every learner gets the same case to decide. */
    public String replay(Path fixturePath) {
        JsonNode fx;
        try {
            fx = Contracts.JSON.readTree(Files.readString(fixturePath, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read fixture " + fixturePath + ": " + e.getMessage());
        }
        String rid = store.createRun(fx.get("account").asText(),
                fx.get("question").asText() + "  [replay of " + fx.get("source_run").asText() + "]");
        advance(new ReplayRunner(fx), rid);
        return rid;
    }

    // --------------------------------------------------------------- helpers
    /** Python's f"{type(e).__name__}: {e}", with the Python class names where they differ. */
    static String describe(Throwable e) {
        String type = e instanceof RunnerException ? "RunnerError" : e.getClass().getSimpleName();
        return type + ": " + e.getMessage();
    }

    @SuppressWarnings("unchecked")
    static Object toMap(JsonNode n) {
        if (n == null || n.isNull()) return null;
        return Contracts.JSON.convertValue(n, Object.class);
    }
}
