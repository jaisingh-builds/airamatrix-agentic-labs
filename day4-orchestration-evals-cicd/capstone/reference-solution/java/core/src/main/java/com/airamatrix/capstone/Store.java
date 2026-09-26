package com.airamatrix.capstone;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Persisted state, SQLite over plain JDBC (the Lab 5.1 store's shape). Every hand-off is a row:
 * the run, the proposal with its SLA snapshot and guardrail verdict, the HUMAN decision (who, why,
 * the hash of what they saw), and the operation id of the one write - stored before it is sent.
 *
 * Run statuses, forward only:
 *   failed | guardrail_intervened | no_action | blocked | awaiting_approval -> approved | rejected
 *   approved -> applied | apply_failed | outcome_unknown (apply again: same operation id)
 */
public final class Store implements AutoCloseable {
    static final ZoneOffset IST = ZoneOffset.ofHoursMinutes(5, 30);
    static final DateTimeFormatter ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx");

    static final String[] SCHEMA = {
        "create table if not exists runs(id text primary key, account_id text, as_of text, question text, mode text, "
            + "status text, cost_usd real, turns integer, tool_calls integer, error text, trace text, created_at text, updated_at text)",
        "create table if not exists proposals(run_id text primary key, proposal text, sla text, verdict text, sha text, trajectory text)",
        "create table if not exists approvals(run_id text primary key, decision text, approver text, principal text, "
            + "reason text, at text, proposal_sha text)",
        "create table if not exists operations(run_id text primary key, op_id text, action text, payload text, status text, "
            + "response text, created_at text, updated_at text)",
    };

    public record Run(String id, String accountId, String asOf, String question, String mode, String status,
                      double costUsd, int turns, int toolCalls, String error, String trace, String createdAt) {}

    public record ProposalRow(JsonNode proposal, JsonNode sla, JsonNode verdict, String sha, JsonNode trajectory) {}

    public record Approval(String decision, String approver, String principal, String reason, String at, String proposalSha) {}

    public record Operation(String opId, String action, JsonNode payload, String status, String response) {}

    /** Two writers raced for a row only one may create (two people deciding one run). */
    public static final class Conflict extends RuntimeException {
        Conflict(String m) { super(m); }
    }

    private final Connection db;

    public Store(String path) {
        try {
            db = DriverManager.getConnection("jdbc:sqlite:" + path);
            try (Statement s = db.createStatement()) { for (String sql : SCHEMA) s.execute(sql); }
        } catch (SQLException e) {
            throw new IllegalStateException("cannot open " + path + ": " + e.getMessage(), e);
        }
    }

    static String now() { return OffsetDateTime.now(IST).format(ISO); }

    public static String newId() { return UUID.randomUUID().toString().replace("-", "").substring(0, 10); }

    public synchronized String createRun(String id, String accountId, String asOf, String question, String mode) {
        exec("insert into runs(id, account_id, as_of, question, mode, status, cost_usd, turns, tool_calls, created_at, updated_at) "
                + "values(?,?,?,?,?,?,0,0,0,?,?)", id, accountId, asOf, question, mode, "created", now(), now());
        return id;
    }

    public synchronized void finishRun(String id, String status, double cost, int turns, int toolCalls, String error, String trace) {
        exec("update runs set status=?, cost_usd=?, turns=?, tool_calls=?, error=?, trace=?, updated_at=? where id=?",
                status, cost, turns, toolCalls, error, trace, now(), id);
    }

    public synchronized void setStatus(String id, String status) {
        exec("update runs set status=?, updated_at=? where id=?", status, now(), id);
    }

    public synchronized Run run(String id) {
        List<Run> r = runs("select * from runs where id=?", id);
        if (r.isEmpty()) throw new IllegalArgumentException("no run " + id);
        return r.get(0);
    }

    public synchronized List<Run> runs(int limit) { return runs("select * from runs order by created_at desc, rowid desc limit ?", limit); }

    public synchronized void saveProposal(String id, JsonNode proposal, JsonNode sla, JsonNode verdict, JsonNode trajectory) {
        exec("insert or replace into proposals values(?,?,?,?,?,?)", id, str(proposal), str(sla), str(verdict), sha(proposal), str(trajectory));
    }

    public synchronized ProposalRow proposal(String id) {
        try (PreparedStatement ps = db.prepareStatement("select * from proposals where run_id=?")) {
            ps.setString(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return null;
                return new ProposalRow(json(rs.getString("proposal")), json(rs.getString("sla")), json(rs.getString("verdict")),
                        rs.getString("sha"), json(rs.getString("trajectory")));
            }
        } catch (SQLException e) { throw new IllegalStateException(e); }
    }

    /** The decision is bound to the proposal as the human saw it. The primary key is the real "one decision" guarantee. */
    public synchronized void recordDecision(String id, String decision, String approver, String principal, String reason, String proposalSha) {
        try {
            exec("insert into approvals values(?,?,?,?,?,?,?)", id, decision, approver, principal, reason, now(), proposalSha);
        } catch (IllegalStateException e) {
            if (e.getCause() instanceof SQLException s && String.valueOf(s.getMessage()).contains("UNIQUE")) {
                throw new Conflict("run " + id + " was already decided");
            }
            throw e;
        }
    }

    public synchronized Approval approval(String id) {
        try (PreparedStatement ps = db.prepareStatement("select * from approvals where run_id=?")) {
            ps.setString(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return null;
                return new Approval(rs.getString("decision"), rs.getString("approver"), rs.getString("principal"),
                        rs.getString("reason"), rs.getString("at"), rs.getString("proposal_sha"));
            }
        } catch (SQLException e) { throw new IllegalStateException(e); }
    }

    public synchronized void recordOperation(String id, String opId, String action, JsonNode payload) {
        exec("insert into operations values(?,?,?,?,?,?,?,?)", id, opId, action, str(payload), "pending", null, now(), now());
    }

    public synchronized void operationResult(String id, String status, String response) {
        exec("update operations set status=?, response=?, updated_at=? where run_id=?", status, Tools.cut(response, 4000), now(), id);
    }

    public synchronized Operation operation(String id) {
        try (PreparedStatement ps = db.prepareStatement("select * from operations where run_id=?")) {
            ps.setString(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return null;
                return new Operation(rs.getString("op_id"), rs.getString("action"), json(rs.getString("payload")),
                        rs.getString("status"), rs.getString("response"));
            }
        } catch (SQLException e) { throw new IllegalStateException(e); }
    }

    /** Fingerprint of the exact proposal a human decides on (canonical JSON: sorted keys). */
    public static String sha(JsonNode n) {
        try {
            Object canonical = Contracts.JSON.convertValue(n, Object.class);
            String text = Contracts.JSON.copy().configure(com.fasterxml.jackson.databind.SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true)
                    .writeValueAsString(sortDeep(canonical));
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) { throw new IllegalStateException(e); }
    }

    @SuppressWarnings("unchecked")
    private static Object sortDeep(Object o) {
        if (o instanceof Map<?, ?> m) {
            Map<String, Object> out = new java.util.TreeMap<>();
            m.forEach((k, v) -> out.put(String.valueOf(k), sortDeep(v)));
            return out;
        }
        if (o instanceof List<?> l) return l.stream().map(Store::sortDeep).toList();
        return o;
    }

    private List<Run> runs(String sql, Object... args) {
        try (PreparedStatement ps = db.prepareStatement(sql)) {
            bind(ps, args);
            List<Run> out = new ArrayList<>();
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new Run(rs.getString("id"), rs.getString("account_id"), rs.getString("as_of"), rs.getString("question"),
                            rs.getString("mode"), rs.getString("status"), rs.getDouble("cost_usd"), rs.getInt("turns"),
                            rs.getInt("tool_calls"), rs.getString("error"), rs.getString("trace"), rs.getString("created_at")));
                }
            }
            return out;
        } catch (SQLException e) { throw new IllegalStateException(e); }
    }

    private void exec(String sql, Object... args) {
        try (PreparedStatement ps = db.prepareStatement(sql)) {
            bind(ps, args);
            ps.executeUpdate();
        } catch (SQLException e) { throw new IllegalStateException(e.getMessage(), e); }
    }

    private static void bind(PreparedStatement ps, Object... args) throws SQLException {
        for (int i = 0; i < args.length; i++) ps.setObject(i + 1, args[i]);
    }

    static String str(JsonNode n) { return n == null ? null : n.toString(); }

    static JsonNode json(String s) {
        if (s == null) return null;
        try { return Contracts.JSON.readTree(s); } catch (Exception e) { throw new IllegalStateException(e); }
    }

    public static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }

    @Override
    public synchronized void close() {
        try { db.close(); } catch (SQLException ignored) { /* closing */ }
    }
}
