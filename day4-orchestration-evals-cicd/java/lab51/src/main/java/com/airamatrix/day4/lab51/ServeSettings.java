package com.airamatrix.day4.lab51;

import java.util.List;
import java.util.function.Function;

import com.airamatrix.day4.common.GatewayAgentRunner;

/**
 * How a {@code serve} process was started. Least privilege per PROCESS, same as the CLI:
 *
 * <ul>
 *   <li>{@code serve} (agents side, port 8170) runs the agent stages. It holds the READ token and
 *       refuses to start if AIRA_OPS_APPLY_TOKEN or AIRA_OPS_TOKEN is set. Its apply endpoint
 *       always refuses: this process has no write credential to use.</li>
 *   <li>{@code serve --apply} (write side, port 8171) holds the APPLY token and never starts a
 *       stage: POST /api/runs, /resume and /api/replay refuse. It can show runs, record decisions
 *       and apply approved ones.</li>
 * </ul>
 *
 * One process holding both tokens would put the write credential next to the agents again - the
 * thing the Python lab's scrub_agent_environment() exists to prevent - so there is no such mode.
 */
public record ServeSettings(boolean applyMode, String opsUrl, String readToken, String applyToken) {

    public static ServeSettings fromEnv(boolean applyMode, Function<String, String> env) {
        String read = applyMode ? "" : nz(env.apply("AIRA_OPS_READ_TOKEN"));
        String write = applyMode ? nz(env.apply("AIRA_OPS_APPLY_TOKEN")) : "";   // only the apply side reads it
        return new ServeSettings(applyMode, Pipeline.opsUrl(env), read, write);
    }

    /** Why this server must not start, or null. */
    public static String startupRefusal(boolean applyMode, Function<String, String> env) {
        if (applyMode) {
            return nz(env.apply("AIRA_OPS_APPLY_TOKEN")).isEmpty()
                    ? "refusing to start `serve --apply`: AIRA_OPS_APPLY_TOKEN is not set - the apply side has its own credential ("
                      + Cli.PROG + " tokens)"
                    : null;
        }
        List<String> held = GatewayAgentRunner.FORBIDDEN_ENV.stream().filter(k -> !nz(env.apply(k)).isEmpty()).toList();
        if (!held.isEmpty()) {
            return "refusing to start `serve`: " + String.join(", ", held) + " is set in this process. The agents server "
                    + "holds no write or admin token - unset it here and start `serve --apply` in another shell for the write.";
        }
        return null;
    }

    public String mode() { return applyMode ? "apply" : "agents"; }

    private static String nz(String s) { return s == null ? "" : s; }
}
