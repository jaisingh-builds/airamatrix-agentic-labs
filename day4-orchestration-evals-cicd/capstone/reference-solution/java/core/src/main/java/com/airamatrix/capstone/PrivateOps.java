package com.airamatrix.capstone;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.ServerSocket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

/**
 * A throwaway aira-ops with fresh seed data on a free port, for evals and tests - the same commands as the
 * Lab 5.2 harness ({@code aira_ops.py --issue-token}, then {@code --reset --db --callers}). Each account gets
 * its own READ-ONLY caller token scoped to that account; an optional write token is for tests of apply.
 * The admin token is random, in memory only, and nobody uses it.
 */
public final class PrivateOps implements AutoCloseable {
    private static final SecureRandom RNG = new SecureRandom();
    private final Path tmp;
    private final Process server;
    public final String url;
    public final Map<String, String> readTokens;
    public final Map<String, String> writeTokens;
    private final Thread hook;

    private PrivateOps(Path tmp, Process server, String url, Map<String, String> read, Map<String, String> write) {
        this.tmp = tmp;
        this.server = server;
        this.url = url;
        this.readTokens = read;
        this.writeTokens = write;
        this.hook = new Thread(server::destroy);
        Runtime.getRuntime().addShutdownHook(hook);
    }

    public static PrivateOps start(List<String> accounts, boolean withWriteTokens) throws Exception {
        Path tmp = Files.createTempDirectory("capstone-ops-");
        String admin = "admin-" + hex(8);
        Path callers = tmp.resolve("callers.json");
        Map<String, String> read = new LinkedHashMap<>(), write = new LinkedHashMap<>();
        Process p = null;
        try {
            for (String acc : accounts) {
                read.put(acc, issue(callers, admin, "sla-responder-" + acc, acc, false));
                if (withWriteTokens) write.put(acc, issue(callers, admin, "capstone-apply-" + acc, acc, true));
            }
            int port;
            try (ServerSocket s = new ServerSocket(0, 0, java.net.InetAddress.getLoopbackAddress())) { port = s.getLocalPort(); }
            ProcessBuilder pb = new ProcessBuilder(Repo.python(), Repo.opsScript().toString(), "--port", String.valueOf(port), "--quiet",
                    "--reset", "--db", tmp.resolve("ops.sqlite").toString(), "--callers", callers.toString())
                    .redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD);
            env(pb, admin);
            p = pb.start();
            String url = "http://127.0.0.1:" + port;
            for (int i = 0; i < 80; i++) {
                if (healthy(url)) return new PrivateOps(tmp, p, url, read, write);
                Thread.sleep(100);
            }
            throw new IllegalStateException("aira-ops did not start (is " + Repo.python() + " on PATH?)");
        } catch (Exception e) {
            if (p != null) p.destroy();
            delete(tmp);
            throw e;
        }
    }

    static String issue(Path callers, String admin, String actor, String account, boolean write) throws Exception {
        List<String> cmd = new ArrayList<>(List.of(Repo.python(), Repo.opsScript().toString(), "--callers", callers.toString(),
                "--issue-token", actor, "--accounts", account));
        if (write) cmd.add("--write");
        ProcessBuilder pb = new ProcessBuilder(cmd).redirectError(ProcessBuilder.Redirect.DISCARD);
        env(pb, admin);
        Process ip = pb.start();
        String tok = new String(ip.getInputStream().readAllBytes(), StandardCharsets.UTF_8).strip();
        if (ip.waitFor() != 0 || tok.isEmpty()) throw new IllegalStateException("aira-ops --issue-token failed");
        return tok;
    }

    /** The child gets the parent's environment minus every secret, plus its own admin token. */
    static void env(ProcessBuilder pb, String admin) {
        pb.environment().keySet().removeIf(k -> com.airamatrix.day4.common.Spans.SECRET_NAMES.matcher(k).find());
        pb.environment().put("AIRA_OPS_TOKEN", admin);
    }

    static boolean healthy(String url) {
        try {
            HttpURLConnection c = (HttpURLConnection) URI.create(url + "/health").toURL().openConnection();
            c.setConnectTimeout(300);
            c.setReadTimeout(300);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (IOException e) {
            return false;
        }
    }

    static String hex(int n) {
        byte[] b = new byte[n];
        RNG.nextBytes(b);
        return HexFormat.of().formatHex(b);
    }

    static void delete(Path dir) {
        try (Stream<Path> s = Files.walk(dir)) {
            s.sorted(Comparator.reverseOrder()).forEach(q -> q.toFile().delete());
        } catch (IOException ignored) { /* temp */ }
    }

    @Override
    public void close() {
        server.destroy();
        try { server.waitFor(10, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        if (server.isAlive()) server.destroyForcibly();
        try { Runtime.getRuntime().removeShutdownHook(hook); } catch (IllegalStateException ignored) { /* shutting down */ }
        delete(tmp);
    }
}
