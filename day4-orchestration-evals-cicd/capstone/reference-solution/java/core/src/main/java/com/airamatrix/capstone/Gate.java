package com.airamatrix.capstone;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * THE HUMAN APPROVAL POINT and the one write.
 *
 * decide(): a named person, a reason, one decision per run, bound to the hash of the proposal they saw.
 *           Refused: no name, no reason (or a one-word one), an agent identity as approver, a run that is not
 *           waiting, a run the guardrail blocked (no override exists), a second decision.
 * apply():  plain code, not an agent, in its own process with the only write credential. Checks the DECISION
 *           RECORD (not the status field), the proposal hash, the write host, the outbound guardrail again and the
 *           ticket's current state; stores the operation id BEFORE sending so a retry writes at most once.
 */
public final class Gate {
    private Gate() {}

    public static final class GateError extends RuntimeException {
        public GateError(String m) { super(m); }
    }

    /** Names an agent or service identity uses. An agent cannot approve its own proposal. */
    public static final Set<String> AGENT_IDENTITIES = Set.of("sla-responder", "capstone-agent", "investigator", "reviewer",
            "supervisor", "pipeline-agents", "pipeline-apply");
    static final Pattern AGENTISH = Pattern.compile("(?i)(^|[^a-z])(agent|bot|runtime|claude|llm)([^a-z]|$)");
    static final Set<String> LOCAL_HOSTS = Set.of("127.0.0.1", "localhost", "::1", "[::1]");

    public static Store.Run decide(Store store, String rid, String decision, String approver, String principal, String reason, Spans tr) {
        try {
            return decideOrRefuse(store, rid, decision, approver, principal, reason, tr);
        } catch (GateError e) {
            refused(tr, decision, e);
            throw e;
        }
    }

    /** A refusal is part of the story of a run: it goes in the trace too (our message, never the human's reason). */
    static void refused(Spans tr, String what, GateError e) {
        tr.event("gate.refused", Map.of("decision", what, "reason", Tools.cut(e.getMessage(), 200)));
    }

    private static Store.Run decideOrRefuse(Store store, String rid, String decision, String approver, String principal, String reason, Spans tr) {
        if (!Set.of("approve", "reject").contains(decision)) throw new IllegalArgumentException("decision must be approve or reject");
        String who = approver == null ? "" : approver.strip(), why = reason == null ? "" : reason.strip();
        if (who.isEmpty() || why.isEmpty()) throw new GateError("a decision needs --by (who) and --reason (why)");
        if (why.length() < 10 || !why.contains(" ")) throw new GateError("--reason must say why in a sentence, not '" + why + "'");
        if (AGENT_IDENTITIES.contains(who.toLowerCase(Locale.ROOT)) || AGENTISH.matcher(who).find()) {
            throw new GateError("'" + who + "' is an agent or service identity - a person decides, not the agent that proposed it");
        }
        Store.Run r = store.run(rid);
        if (store.approval(rid) != null) throw new GateError("run " + rid + " was already decided");
        if (r.status().equals("blocked")) {
            List<String> rules = Guardrails.Verdict.fromJson(store.proposal(rid).verdict()).rules();
            throw new GateError("the guardrail blocked this proposal (" + String.join(", ", rules) + "). "
                    + "There is no override: fix the cause and run again.");
        }
        if (!r.status().equals("awaiting_approval")) throw new GateError("run " + rid + " is " + r.status() + ", not waiting for a decision");
        Store.ProposalRow p = store.proposal(rid);
        try {
            store.recordDecision(rid, decision, who, principal, why, p.sha());
        } catch (Store.Conflict e) {
            throw new GateError(e.getMessage());
        }
        store.setStatus(rid, decision.equals("approve") ? "approved" : "rejected");
        tr.event("gate.decided", Map.of("decision", decision, "approver", who));    // the reason stays in the store, not the trace
        return store.run(rid);
    }

    /** What apply needs from aira-ops, with the WRITE credential. Tests pass a fake; real: {@link HttpOpsWriter}. */
    public interface OpsWriter {
        record Resp(int status, JsonNode body) {}
        Resp getTicket(String ticketId);
        Resp postComment(String ticketId, String comment, String idempotencyKey);
    }

    public static Store.Run apply(Store store, String rid, OpsWriter writer, String opsUrl, Spans tr) {
        try {
            return applyOrRefuse(store, rid, writer, opsUrl, tr);
        } catch (GateError e) {
            refused(tr, "apply", e);
            throw e;
        }
    }

    private static Store.Run applyOrRefuse(Store store, String rid, OpsWriter writer, String opsUrl, Spans tr) {
        Store.Run r = store.run(rid);
        Store.Approval a = store.approval(rid);
        if (a == null || !a.decision().equals("approve")) throw new GateError("run " + rid + " has no approval on record");
        Store.ProposalRow p = store.proposal(rid);
        if (p == null || !Store.sha(p.proposal()).equals(a.proposalSha())) {
            throw new GateError("run " + rid + ": the proposal changed after it was decided - it needs a new decision");
        }
        if (r.status().equals("applied")) return r;                     // applying again is a no-op, not an error
        if (!Set.of("approved", "outcome_unknown").contains(r.status())) {
            throw new GateError("run " + rid + " is " + r.status() + "; only an approved run can be applied");
        }
        if (!"local".equals(r.mode())) {
            throw new GateError("run " + rid + " read the SHARED aira-ops through the AgentCore Gateway. The decision is recorded; "
                    + "the write is made in local mode only (classroom rule: nobody writes to the shared aira-ops)");
        }
        String host = URI.create(opsUrl).getHost();
        String allowed = System.getenv("CAPSTONE_ALLOW_WRITE_HOST");
        if (!LOCAL_HOSTS.contains(host) && !host.equals(allowed)) {
            throw new GateError("refusing to write to " + host + ": apply writes only to your own aira-ops on this machine "
                    + "(set CAPSTONE_ALLOW_WRITE_HOST to that host name to allow another)");
        }
        JsonNode action = p.proposal().path("action");
        if (!action.path("type").asText().equals("post_customer_update")) throw new GateError("run " + rid + " has nothing to apply");
        String tid = action.path("ticket_id").asText(), comment = action.path("comment").asText();
        List<Guardrails.Denial> again = Guardrails.outbound(comment, Sla.fromJson(p.sla()));   // defence in depth, at the write
        if (!again.isEmpty()) throw new GateError("outbound guardrail refused the comment: " + again.get(0).rule());

        Store.Operation op = store.operation(rid);
        if (op == null) {                                                // stored BEFORE the request is sent
            ObjectNode payload = Contracts.object().put("ticket_id", tid).put("comment", comment);
            store.recordOperation(rid, UUID.randomUUID().toString(), "post_customer_update", payload);
            op = store.operation(rid);
        }
        if (op.status().equals("done")) { store.setStatus(rid, "applied"); return store.run(rid); }

        try (Spans.Span sp = tr.span("apply", Store.map("action", "post_customer_update", "op_id", op.opId(), "approver", a.approver(),
                "input", Map.of("ticket_id", tid)))) {
            OpsWriter.Resp now = writer.getTicket(tid);                  // the world may have moved while the human decided
            if (now.status() == 404) { sp.fail("not visible to the write credential"); throw new GateError(tid + " is not visible to the apply credential"); }
            String st = now.body().path("status").asText();
            if (now.status() == 200 && !Sla.OPEN_TICKET.contains(st)) {
                sp.fail("stale: ticket is " + st);
                throw new GateError(tid + " is " + st + " now - the update is stale; nothing was written");
            }
            OpsWriter.Resp resp = writer.postComment(tid, comment, op.opId());
            sp.set("http_status", resp.status()).set("replayed", resp.body().path("_replayed").asBoolean(false));
            if (resp.status() == 200 || resp.status() == 201) {
                store.operationResult(rid, "done", "HTTP " + resp.status());
                store.setStatus(rid, "applied");
            } else if (resp.status() == 0 || resp.status() >= 500) {
                store.operationResult(rid, "pending", resp.body().toString());
                store.setStatus(rid, "outcome_unknown");
                sp.fail("outcome unknown - run apply again; the same operation id makes it safe");
            } else {
                store.operationResult(rid, "failed", resp.body().toString());
                store.setStatus(rid, "apply_failed");
                sp.fail("HTTP " + resp.status());
            }
        }
        return store.run(rid);
    }

    /** aira-ops with the apply token: one read (current state) and one idempotent comment. */
    public static final class HttpOpsWriter implements OpsWriter {
        private final String base, token;
        private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
        private final Duration timeout;

        public HttpOpsWriter(String base, String token, Duration timeout) {
            this.base = base.replaceAll("/+$", "");
            this.token = token;
            this.timeout = timeout;
        }

        @Override public Resp getTicket(String id) {
            return send(HttpRequest.newBuilder(URI.create(base + "/tickets/" + id)).GET());
        }

        @Override public Resp postComment(String id, String comment, String key) {
            String body = Contracts.object().put("body", comment).toString();
            return send(HttpRequest.newBuilder(URI.create(base + "/tickets/" + id + "/comments"))
                    .header("Content-Type", "application/json").header("Idempotency-Key", key)
                    .POST(HttpRequest.BodyPublishers.ofString(body)));
        }

        private Resp send(HttpRequest.Builder b) {
            try {
                HttpResponse<String> r = http.send(b.timeout(timeout).header("Authorization", "Bearer " + token).build(),
                        HttpResponse.BodyHandlers.ofString());
                return new Resp(r.statusCode(), r.body().isEmpty() ? Contracts.object() : Contracts.JSON.readTree(r.body()));
            } catch (IOException e) {
                return new Resp(0, Contracts.object().put("error", Spans.redact(e.getClass().getSimpleName())));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return new Resp(0, Contracts.object().put("error", "interrupted"));
            }
        }
    }
}
