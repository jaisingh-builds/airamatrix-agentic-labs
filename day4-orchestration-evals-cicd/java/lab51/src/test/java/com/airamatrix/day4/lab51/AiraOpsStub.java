package com.airamatrix.day4.lab51;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

/**
 * A local stand-in for aira-ops' write API (day3-integration-security/aira-ops/aira_ops.py), just
 * enough to test the apply step offline: per-caller tokens (read vs write), Idempotency-Key replay
 * with "_replayed", optimistic concurrency on config (expected_version), ticket comments, an audit
 * trail, optional latency (to force a client timeout) and a forced status (to fake a 5xx / 4xx).
 */
class AiraOpsStub implements AutoCloseable {
    static final String READ_TOKEN = "read-token-for-tests";
    static final String WRITE_TOKEN = "write-token-for-tests";

    record Config(JsonNode value, int version) {}
    record Stored(String fingerprint, int status, String body) {}

    final HttpServer server;
    final ExecutorService pool = Executors.newCachedThreadPool();
    final String url;
    final Map<String, String> actors = Map.of(READ_TOKEN, "pipeline-agents", WRITE_TOKEN, "pipeline-apply");
    final Map<String, Boolean> canWrite = Map.of("pipeline-agents", false, "pipeline-apply", true);
    final Map<String, Config> config = new HashMap<>();
    final Map<String, List<String>> comments = new HashMap<>();
    final Map<String, Stored> idem = new HashMap<>();
    final List<String> auditActors = new ArrayList<>();
    final AtomicInteger writesApplied = new AtomicInteger();
    final List<String> idempotencyKeys = new ArrayList<>();
    volatile long latencyMs = 0;
    volatile int forceStatus = 0;

    AiraOpsStub() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(pool);
        server.createContext("/", this::handle);
        server.start();
        url = "http://127.0.0.1:" + server.getAddress().getPort();
        config.put("ingest.max_concurrent_jobs", new Config(Contracts.JSON.getNodeFactory().numberNode(4), 1));
        config.put("alerts.ingest_latency_minutes", new Config(Contracts.JSON.getNodeFactory().numberNode(15), 1));
    }

    @Override
    public void close() {
        server.stop(0);
        pool.shutdownNow();
    }

    private void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        String raw = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        try {
            if (latencyMs > 0) Thread.sleep(latencyMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        String auth = String.valueOf(ex.getRequestHeaders().getFirst("Authorization"));
        String actor = actors.get(auth.replaceFirst("^Bearer ", ""));
        int status;
        String body;
        synchronized (this) {
            if (actor == null) {
                status = 401; body = err("unauthorized", "missing or unknown token");
            } else if (method.equals("GET")) {
                if (path.startsWith("/config/") && config.containsKey(path.substring(8))) {
                    Config c = config.get(path.substring(8));
                    status = 200; body = configBody(path.substring(8), c);
                } else {
                    status = 404; body = err("not_found", "no route");
                }
            } else if (!canWrite.get(actor)) {
                status = 403; body = err("forbidden", "this caller token is read-only");
            } else {
                String key = ex.getRequestHeaders().getFirst("Idempotency-Key");
                if (key == null) {
                    status = 400; body = err("invalid", "writes require an Idempotency-Key header");
                } else {
                    idempotencyKeys.add(key);
                    String fp = method + " " + path + "\n" + raw;
                    Stored prior = idem.get(actor + "|" + key);
                    if (prior != null) {
                        if (!prior.fingerprint().equals(fp)) {
                            status = 422; body = err("invalid", "Idempotency-Key was already used for a different request");
                        } else {
                            ObjectNode b = (ObjectNode) FakeRunner.json(prior.body());
                            b.put("_replayed", true);
                            status = prior.status(); body = b.toString();
                        }
                    } else if (forceStatus != 0) {
                        status = forceStatus; body = err(forceStatus >= 500 ? "internal" : "conflict", "forced by the test");
                    } else {
                        int[] st = new int[1];
                        body = write(method, path, FakeRunner.json(raw.isEmpty() ? "{}" : raw), actor, st);
                        status = st[0];
                        idem.put(actor + "|" + key, new Stored(fp, status, body));
                    }
                }
            }
        }
        byte[] out = body.getBytes(StandardCharsets.UTF_8);
        try {
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, out.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(out); }
        } catch (IOException gone) {
            // the client timed out and left - the write above still happened (that is the point)
        } finally {
            ex.close();
        }
    }

    private String write(String method, String path, JsonNode body, String actor, int[] status) {
        if (method.equals("PUT") && path.startsWith("/config/")) {
            String key = path.substring(8);
            Config c = config.get(key);
            if (c == null) { status[0] = 404; return err("not_found", "no config " + key); }
            if (body.path("expected_version").asInt() != c.version()) {
                status[0] = 409; return err("conflict", "version is " + c.version());
            }
            Config n = new Config(body.get("value"), c.version() + 1);
            config.put(key, n);
            auditActors.add(actor);
            writesApplied.incrementAndGet();
            status[0] = 200;
            return configBody(key, n);
        }
        if (method.equals("POST") && path.matches("/tickets/T-\\d{4}/comments")) {
            String ticket = path.split("/")[2];
            comments.computeIfAbsent(ticket, k -> new ArrayList<>()).add(body.path("body").asText());
            auditActors.add(actor);
            writesApplied.incrementAndGet();
            status[0] = 201;
            ObjectNode o = Contracts.object().put("ticket_id", ticket);
            o.putObject("comment").put("body", body.path("body").asText()).put("author", actor);
            return o.toString();
        }
        status[0] = 404;
        return err("not_found", "no route " + method + " " + path);
    }

    private static String configBody(String key, Config c) {
        ObjectNode o = Contracts.object().put("key", key);
        o.set("value", c.value());
        o.put("version", c.version());
        return o.toString();
    }

    private static String err(String code, String message) {
        ObjectNode o = Contracts.object();
        o.putObject("error").put("code", code).put("message", message);
        return o.toString();
    }
}
