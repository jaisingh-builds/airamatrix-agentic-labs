package com.airamatrix.day4.lab51;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
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
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.NullNode;

/**
 * The shared state store: the ONLY thing the stages have in common. Port of
 * lab5-1-handoff/store.py - same tables, same columns, same semantics, so a runs.sqlite
 * written by one can be read by the other.
 *
 * Stages never call each other. Each reads what it needs from here and writes its result back.
 * That makes the pipeline checkpointed (a crash loses at most the stage in flight), resumable
 * (re-running skips finished stages), inspectable (every hand-off is a row you can read) and
 * gateable (a human decision is a row too, with a name and a reason).
 *
 * SQLite over plain JDBC, one connection, every method synchronized (the Python lock).
 */
public class Store implements AutoCloseable {

    static final ZoneOffset IST = ZoneOffset.ofHoursMinutes(5, 30);
    private static final DateTimeFormatter ISO_SECONDS = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx");

    static String now() {
        return OffsetDateTime.now(IST).format(ISO_SECONDS);
    }

    static final String[] SCHEMA = {
        "create table if not exists runs(id text primary key, account_id text, question text, "
            + "status text, created_at text, updated_at text)",
        "create table if not exists stages(run_id text, name text, status text, attempt integer, "
            + "output text, error text, cost_usd real, tool_calls integer, started_at text, finished_at text, "
            + "primary key(run_id, name))",
        "create table if not exists approvals(run_id text primary key, decision text, approver text, "
            + "reason text, override integer, at text, proposal_sha text)",
        "create table if not exists operations(run_id text primary key, op_id text, action text, "
            + "payload text, status text, response text, created_at text, updated_at text)",
    };

    // Run statuses, in order. The pipeline moves forward only.
    //   created -> investigated -> reviewed -> awaiting_approval -> approved -> applied
    //                                      \-> needs_rework        \-> rejected
    //   apply can end in: applied | apply_failed | outcome_unknown (apply again retries safely)
    public static final List<String> TERMINAL = List.of("applied", "rejected", "no_change");

    /** Python raises KeyError("no run X"); the CLI prints it the same way: refused: 'no run X'. */
    public static class NoSuchRun extends RuntimeException {
        public NoSuchRun(String rid) { super("no run " + rid); }
    }

    /** Two writers raced for a row only one of them may create (e.g. two people deciding one run). */
    public static class Conflict extends RuntimeException {
        public Conflict(String message) { super(message); }
    }

    public record RunRow(String id, String accountId, String question, String status, String createdAt, String updatedAt) {}

    public record StageRow(String runId, String name, String status, int attempt, JsonNode output, String error,
                           double costUsd, int toolCalls, String startedAt, String finishedAt) {}

    public record ApprovalRow(String runId, String decision, String approver, String reason, int override,
                              String at, String proposalSha) {}

    public record OperationRow(String runId, String opId, String action, JsonNode payload, String status,
                               String response, String createdAt, String updatedAt) {}

    private final Connection db;
    public final String path;

    public Store(String path) {
        this.path = path;
        try {
            db = DriverManager.getConnection("jdbc:sqlite:" + path);
            try (Statement st = db.createStatement()) {
                st.execute("pragma busy_timeout = 5000");       // the CLI and a server may share the file
                for (String ddl : SCHEMA) st.execute(ddl);
                Set<String> cols = new HashSet<>();
                try (ResultSet rs = st.executeQuery("pragma table_info(approvals)")) {
                    while (rs.next()) cols.add(rs.getString(2));
                }
                if (!cols.contains("proposal_sha")) {           // a runs.sqlite from before this column existed
                    st.execute("alter table approvals add column proposal_sha text");
                }
            }
        } catch (SQLException e) {
            throw new IllegalStateException("cannot open " + path + ": " + e.getMessage(), e);
        }
    }

    @Override
    public synchronized void close() {
        try { db.close(); } catch (SQLException ignored) { /* closing */ }
    }

    // ---- runs
    public synchronized String createRun(String accountId, String question) {
        String rid = UUID.randomUUID().toString().replace("-", "").substring(0, 10);
        update("insert into runs values(?,?,?,?,?,?)", rid, accountId, question, "created", now(), now());
        return rid;
    }

    public synchronized RunRow run(String rid) {
        List<RunRow> rows = query("select * from runs where id=?", this::runRow, rid);
        if (rows.isEmpty()) throw new NoSuchRun(rid);
        return rows.get(0);
    }

    public synchronized List<RunRow> runs(int limit) {
        return query("select * from runs order by created_at desc limit ?", this::runRow, limit);
    }

    public List<RunRow> runs() { return runs(20); }

    public synchronized void setStatus(String rid, String status) {
        update("update runs set status=?, updated_at=? where id=?", status, now(), rid);
    }

    // ---- stages: the checkpoint
    public synchronized StageRow stage(String rid, String name) {
        List<StageRow> rows = query("select * from stages where run_id=? and name=?", this::stageRow, rid, name);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public synchronized int stageStarted(String rid, String name) {
        StageRow prev = stage(rid, name);
        int attempt = prev != null ? prev.attempt() + 1 : 1;
        double spent = prev != null ? prev.costUsd() : 0.0;          // failed attempts were paid for too
        update("insert or replace into stages values(?,?,?,?,?,?,?,?,?,?)",
                rid, name, "running", attempt, null, null, spent, 0, now(), null);
        return attempt;
    }

    public synchronized void stageDone(String rid, String name, JsonNode output, double costUsd, int toolCalls) {
        update("update stages set status='done', output=?, cost_usd=cost_usd+?, tool_calls=?, finished_at=?, error=null "
                + "where run_id=? and name=?", PyJson.dumps(output), costUsd, toolCalls, now(), rid, name);
    }

    public synchronized void stageFailed(String rid, String name, String error, double costUsd) {
        String e = String.valueOf(error);
        update("update stages set status='failed', error=?, cost_usd=cost_usd+?, finished_at=? where run_id=? and name=?",
                e.length() > 500 ? e.substring(0, 500) : e, costUsd, now(), rid, name);
    }

    // ---- the human gate
    public synchronized ApprovalRow approval(String rid) {
        List<ApprovalRow> rows = query("select * from approvals where run_id=?", rs -> new ApprovalRow(
                rs.getString("run_id"), rs.getString("decision"), rs.getString("approver"), rs.getString("reason"),
                rs.getInt("override"), rs.getString("at"), rs.getString("proposal_sha")), rid);
        return rows.isEmpty() ? null : rows.get(0);
    }

    /** Fingerprint of the exact change a human is deciding on: sha256(json.dumps(change, sort_keys=True)). */
    public synchronized String proposalSha(String rid) {
        StageRow st = stage(rid, "investigate");
        JsonNode change;
        if (st == null) change = Contracts.object();                           // Python: {}
        else if (st.output() == null || st.output().isEmpty()) change = st.output() == null ? NullNode.instance : st.output();
        else change = st.output().has("proposed_change") ? st.output().get("proposed_change") : NullNode.instance;
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(PyJson.dumpsSorted(change).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(h);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public synchronized void recordDecision(String rid, String decision, String approver, String reason, boolean override) {
        // The decision is bound to the proposal as it was when the human saw it.
        try (PreparedStatement ps = db.prepareStatement("insert into approvals(run_id, decision, approver, reason, override, at, proposal_sha) "
                + "values(?,?,?,?,?,?,?)")) {
            bind(ps, rid, decision, approver, reason, override ? 1 : 0, now(), proposalSha(rid));
            ps.executeUpdate();
        } catch (SQLException e) {
            // the primary key is the real "one decision" guarantee
            if (String.valueOf(e.getMessage()).contains("SQLITE_CONSTRAINT")) {
                throw new Conflict("run " + rid + " was already decided");
            }
            throw new IllegalStateException(e);
        }
    }

    // ---- the write: its operation id is stored BEFORE it is sent
    public synchronized OperationRow operation(String rid) {
        List<OperationRow> rows = query("select * from operations where run_id=?", rs -> new OperationRow(
                rs.getString("run_id"), rs.getString("op_id"), rs.getString("action"), parse(rs.getString("payload")),
                rs.getString("status"), rs.getString("response"), rs.getString("created_at"), rs.getString("updated_at")), rid);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public synchronized void recordOperation(String rid, String opId, String action, JsonNode payload) {
        update("insert into operations values(?,?,?,?,?,?,?,?)",
                rid, opId, action, PyJson.dumps(payload), "pending", null, now(), now());
    }

    public synchronized void operationResult(String rid, String status, JsonNode response) {
        String r = PyJson.dumps(response);
        update("update operations set status=?, response=?, updated_at=? where run_id=?",
                status, r.length() > 4000 ? r.substring(0, 4000) : r, now(), rid);
    }

    public synchronized double cost(String rid) {
        List<Double> v = query("select coalesce(sum(cost_usd),0) from stages where run_id=?", rs -> rs.getDouble(1), rid);
        return PyJson.round4(v.get(0));
    }

    // ---- plumbing
    private RunRow runRow(ResultSet rs) throws SQLException {
        return new RunRow(rs.getString("id"), rs.getString("account_id"), rs.getString("question"),
                rs.getString("status"), rs.getString("created_at"), rs.getString("updated_at"));
    }

    private StageRow stageRow(ResultSet rs) throws SQLException {
        return new StageRow(rs.getString("run_id"), rs.getString("name"), rs.getString("status"), rs.getInt("attempt"),
                parse(rs.getString("output")), rs.getString("error"), rs.getDouble("cost_usd"), rs.getInt("tool_calls"),
                rs.getString("started_at"), rs.getString("finished_at"));
    }

    static JsonNode parse(String s) {
        if (s == null || s.isEmpty()) return null;
        try {
            return Contracts.JSON.readTree(s);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("bad JSON in store: " + e.getOriginalMessage());
        }
    }

    interface Mapper<T> { T map(ResultSet rs) throws SQLException; }

    private <T> List<T> query(String sql, Mapper<T> mapper, Object... args) {
        try (PreparedStatement ps = db.prepareStatement(sql)) {
            bind(ps, args);
            try (ResultSet rs = ps.executeQuery()) {
                List<T> out = new ArrayList<>();
                while (rs.next()) out.add(mapper.map(rs));
                return out;
            }
        } catch (SQLException e) {
            throw new IllegalStateException(e.getMessage(), e);
        }
    }

    private void update(String sql, Object... args) {
        try (PreparedStatement ps = db.prepareStatement(sql)) {
            bind(ps, args);
            ps.executeUpdate();
        } catch (SQLException e) {
            throw new IllegalStateException(e.getMessage(), e);
        }
    }

    private static void bind(PreparedStatement ps, Object... args) throws SQLException {
        for (int i = 0; i < args.length; i++) ps.setObject(i + 1, args[i]);
    }
}
