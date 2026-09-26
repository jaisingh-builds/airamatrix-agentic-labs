package com.airamatrix.day4.lab51;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;

/**
 * The pipeline over HTTP - the Spring value-add. Same store, same stages, same gate rules as the
 * CLI (every endpoint calls {@link Pipeline}); nothing here can open the gate that the CLI cannot.
 *
 * <pre>
 *   GET  /api/health                         {"mode": "agents" | "apply"}
 *   GET  /api/runs                           the 20 latest runs
 *   GET  /api/runs/{id}                      one run: stages, decision, operation
 *   POST /api/runs            {account, question}      agents side: runs both stages SYNCHRONOUSLY, returns at the gate
 *   POST /api/runs/{id}/resume                          agents side: finish unfinished stages; never writes
 *   POST /api/replay          {fixture?}                agents side: replay a saved run ($0, no model)
 *   POST /api/runs/{id}/approve  {by, reason, override}
 *   POST /api/runs/{id}/reject   {by, reason}
 *   POST /api/runs/{id}/apply                           apply side only (serve --apply)
 * </pre>
 *
 * Refusals come back as 409 {"error": "refused: ..."} (the CLI's words), an unknown run as 404,
 * a failed stage as 502 with the run id and how to retry, a wrong-side call as 403.
 * The server binds 127.0.0.1 only: there is no authentication, and "by" is a typed name, exactly
 * like the CLI's --by (a classroom simplification; production puts real identity in front).
 */
@RestController
@ConditionalOnWebApplication
@RequestMapping("/api")
public class RunsController {

    private final Store store;
    private final Pipeline pipeline;
    private final ServeSettings settings;
    private final Cli.RunnerFactory runners;

    public RunsController(Store store, ServeSettings settings, Cli.RunnerFactory runners) {
        this.store = store;
        this.pipeline = new Pipeline(store);
        this.settings = settings;
        this.runners = runners;
    }

    public record NewRun(String account, String question) {}

    public record Decision(String by, String reason, Boolean override) {}

    public record Replay(String fixture) {}

    /** A request the server refuses by design (wrong side, missing credential). */
    static class Forbidden extends RuntimeException {
        Forbidden(String m) { super(m); }
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("mode", settings.mode(), "db", store.path);
    }

    @GetMapping("/runs")
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Store.RunRow r : store.runs()) out.add(summary(r));
        return out;
    }

    @GetMapping("/runs/{id}")
    public Map<String, Object> show(@PathVariable String id) {
        return detail(id);
    }

    @PostMapping("/runs")
    public ResponseEntity<Map<String, Object>> create(@RequestBody NewRun body) {
        agentsSideOnly("POST /api/runs");
        if (body == null || blank(body.account()) || blank(body.question())) {
            return ResponseEntity.badRequest().body(Map.of("error", "account and question are required"));
        }
        if (settings.readToken().isEmpty()) {
            throw new Forbidden("AIRA_OPS_READ_TOKEN is not set - agents get a read-only caller token (" + Cli.PROG + " tokens)");
        }
        var runner = runners.create(settings.opsUrl(), settings.readToken());
        String rid = store.createRun(body.account(), body.question());
        try {
            pipeline.advance(runner, rid);
        } catch (RunnerException e) {
            return stageFailed(rid, e);
        }
        return ResponseEntity.status(HttpStatus.CREATED).body(detail(rid));
    }

    @PostMapping("/runs/{id}/resume")
    public ResponseEntity<Map<String, Object>> resume(@PathVariable String id) {
        agentsSideOnly("resume");
        store.run(id);
        if (settings.readToken().isEmpty()) {
            throw new Forbidden("AIRA_OPS_READ_TOKEN is not set - agents get a read-only caller token (" + Cli.PROG + " tokens)");
        }
        try {
            pipeline.resume(runners.create(settings.opsUrl(), settings.readToken()), id);
        } catch (RunnerException e) {
            return stageFailed(id, e);
        }
        return ResponseEntity.ok(detail(id));
    }

    @PostMapping("/replay")
    public ResponseEntity<Map<String, Object>> replay(@RequestBody(required = false) Replay body) {
        agentsSideOnly("replay");
        String fixture = body == null || blank(body.fixture()) ? "fixtures/blocked-36cc478fce.json" : body.fixture();
        String rid = pipeline.replay(Cli.resolveFixture(fixture));
        return ResponseEntity.status(HttpStatus.CREATED).body(detail(rid));
    }

    @PostMapping("/runs/{id}/approve")
    public Map<String, Object> approve(@PathVariable String id, @RequestBody(required = false) Decision d) {
        return decide(id, "approve", d);
    }

    @PostMapping("/runs/{id}/reject")
    public Map<String, Object> reject(@PathVariable String id, @RequestBody(required = false) Decision d) {
        return decide(id, "reject", d);
    }

    private Map<String, Object> decide(String id, String decision, Decision d) {
        Decision x = d == null ? new Decision(null, null, false) : d;
        pipeline.decide(id, decision, x.by(), x.reason(), Boolean.TRUE.equals(x.override()));
        return detail(id);
    }

    @PostMapping("/runs/{id}/apply")
    public Map<String, Object> apply(@PathVariable String id) {
        if (!settings.applyMode()) {
            throw new Forbidden("this is the agents server: it holds no write token and never writes. Apply from the CLI in a "
                    + "shell that holds AIRA_OPS_APPLY_TOKEN, or from a separate `serve --apply` process (port 8171).");
        }
        if (settings.applyToken().isEmpty()) {
            throw new Forbidden("AIRA_OPS_APPLY_TOKEN is not set - the apply step has its own credential (" + Cli.PROG + " tokens)");
        }
        pipeline.apply(id, settings.applyToken(), settings.opsUrl());
        return detail(id);
    }

    // ---- errors: the CLI's words, as HTTP
    @ExceptionHandler({Pipeline.GateError.class, Contracts.ContractError.class, Store.Conflict.class})
    ResponseEntity<Map<String, Object>> refused(RuntimeException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", "refused: " + e.getMessage()));
    }

    @ExceptionHandler(Store.NoSuchRun.class)
    ResponseEntity<Map<String, Object>> notFound(Store.NoSuchRun e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "refused: '" + e.getMessage() + "'"));
    }

    @ExceptionHandler(Forbidden.class)
    ResponseEntity<Map<String, Object>> forbidden(Forbidden e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Map.of("error", "refused: " + e.getMessage()));
    }

    @ExceptionHandler({IllegalArgumentException.class, IllegalStateException.class})
    ResponseEntity<Map<String, Object>> bad(RuntimeException e) {
        return ResponseEntity.badRequest().body(Map.of("error", Spans.redact(String.valueOf(e.getMessage()))));
    }

    private void agentsSideOnly(String what) {
        if (settings.applyMode()) {
            throw new Forbidden("this is the apply server (serve --apply): it holds the write token and never starts a stage - "
                    + what + " belongs on the agents server (serve, port 8170) or the CLI.");
        }
    }

    private ResponseEntity<Map<String, Object>> stageFailed(String rid, RunnerException e) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", "stage failed (recorded, finished stages kept): " + Spans.redact(e.getMessage()));
        body.put("run", rid);
        body.put("retry", "POST /api/runs/" + rid + "/resume");
        body.put("trace", Cli.TRACE_VIEW + " --latest lab5-1-" + rid);
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(body);
    }

    // ---- views
    private Map<String, Object> summary(Store.RunRow r) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", r.id());
        m.put("account_id", r.accountId());
        m.put("question", r.question());
        m.put("status", r.status());
        m.put("cost_usd", store.cost(r.id()));
        m.put("created_at", r.createdAt());
        m.put("updated_at", r.updatedAt());
        return m;
    }

    Map<String, Object> detail(String rid) {
        Map<String, Object> m = summary(store.run(rid));
        Map<String, Object> stages = new LinkedHashMap<>();
        for (String name : List.of("investigate", "review")) {
            Store.StageRow s = store.stage(rid, name);
            if (s == null) continue;
            Map<String, Object> st = new LinkedHashMap<>();
            st.put("status", s.status());
            st.put("attempt", s.attempt());
            st.put("tool_calls", s.toolCalls());
            st.put("cost_usd", PyJson.round4(s.costUsd()));
            st.put("output", s.output());
            st.put("error", s.error());
            stages.put(name, st);
        }
        m.put("stages", stages);
        Store.ApprovalRow a = store.approval(rid);
        if (a != null) {
            Map<String, Object> g = new LinkedHashMap<>();
            g.put("decision", a.decision());
            g.put("approver", a.approver());
            g.put("reason", a.reason());
            g.put("override", a.override() != 0);
            g.put("at", a.at());
            g.put("proposal_sha", a.proposalSha());
            m.put("approval", g);
        }
        Store.OperationRow op = store.operation(rid);
        if (op != null) {
            Map<String, Object> o = new LinkedHashMap<>();
            o.put("status", op.status());
            o.put("op_id", op.opId());
            o.put("action", op.action());
            o.put("payload", op.payload());
            o.put("response", Store.parse(op.response()));
            m.put("operation", o);
        }
        return m;
    }

    private static boolean blank(String s) { return s == null || s.isBlank(); }
}
