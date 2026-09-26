package com.airamatrix.day4.lab53;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

/** Run a subprocess (git) and capture stdout (bytes) and stderr (text) without deadlocking. */
final class Proc {
    private Proc() {}

    record Result(int code, byte[] out, String err) {
        String text() { return new String(out, StandardCharsets.UTF_8); }
    }

    static Result run(List<String> cmd, Path cwd, Map<String, String> extraEnv, long timeoutSeconds) throws IOException {
        ProcessBuilder pb = new ProcessBuilder(cmd);
        if (cwd != null) pb.directory(cwd.toFile());
        if (extraEnv != null) pb.environment().putAll(extraEnv);
        Process p = pb.start();
        p.getOutputStream().close();                       // nothing on stdin
        CompletableFuture<byte[]> out = CompletableFuture.supplyAsync(() -> readAll(p.getInputStream()));
        CompletableFuture<byte[]> err = CompletableFuture.supplyAsync(() -> readAll(p.getErrorStream()));
        try {
            if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                throw new IOException(String.join(" ", cmd) + " timed out after " + timeoutSeconds + " seconds");
            }
            return new Result(p.exitValue(), out.get(), new String(err.get(), StandardCharsets.UTF_8));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            p.destroyForcibly();
            throw new IOException("interrupted: " + String.join(" ", cmd));
        } catch (ExecutionException e) {
            throw new IOException(e.getCause());
        }
    }

    private static byte[] readAll(InputStream in) {
        try (in) {
            return in.readAllBytes();
        } catch (IOException e) {
            return new byte[0];
        }
    }

    /** Python's repr of an argv list, for "Command '[...]' returned non-zero exit status N." */
    static String pyRepr(List<String> cmd) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < cmd.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append('\'').append(cmd.get(i).replace("\\", "\\\\").replace("'", "\\'")).append('\'');
        }
        return sb.append(']').toString();
    }
}
