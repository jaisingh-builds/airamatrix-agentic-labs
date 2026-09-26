package com.airamatrix.agentcore.tools;


import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.core.client.config.ClientOverrideConfiguration;
import software.amazon.awssdk.core.retry.RetryPolicy;
import software.amazon.awssdk.core.sync.ResponseTransformer;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;

/**
 * Step 7 in Java: 07-run/invoke.py for the Java runtimes - same flags, same output.
 *
 *   invoke supervisor "Triage ticket T-1001"
 *   invoke supervisor "What did we decide about T-1001?" --session <id>   [--actor ops-team]
 */
public final class Invoke {
    public static final List<String> AGENTS = List.of("supervisor", "investigator", "reviewer");

    public record Request(String agent, String prompt, String session, String actor) {}

    /** Parses [agent, prompt, --session x, --actor y]; a new session id is at least 33 characters. */
    public static Request parse(List<String> args) {
        List<String> pos = new ArrayList<>();
        String session = null, actor = "ops-team";
        for (int i = 0; i < args.size(); i++) {
            String a = args.get(i);
            if (a.equals("--session") || a.equals("--actor")) {
                if (i + 1 >= args.size()) throw new IllegalArgumentException(a + " needs a value");
                if (a.equals("--session")) session = args.get(++i); else actor = args.get(++i);
            } else pos.add(a);
        }
        if (pos.size() != 2) throw new IllegalArgumentException("usage: invoke <supervisor|investigator|reviewer> \"<prompt>\" [--session ID] [--actor ID]");
        if (!AGENTS.contains(pos.get(0))) throw new IllegalArgumentException("agent must be one of " + AGENTS + ", not " + pos.get(0));
        if (session != null && session.length() < 33) throw new IllegalArgumentException("--session must be at least 33 characters");
        return new Request(pos.get(0), pos.get(1), session == null ? "triage-" + UUID.randomUUID().toString().replace("-", "") : session, actor);
    }

    public static void run(Env env, State state, Request req) throws Exception {
        JsonNode runtimes = state.need("java_runtimes");
        String arn = runtimes.path(req.agent()).asText(null);
        if (arn == null) throw new IllegalStateException("java_runtimes has no " + req.agent() + " - run deploy first");
        // An agent run can take minutes: a long read timeout and NO automatic retry (a retry = a second, parallel run)
        try (BedrockAgentCoreClient rt = BedrockAgentCoreClient.builder().region(env.awsRegion())
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofMinutes(15)).build())
                .overrideConfiguration(ClientOverrideConfiguration.builder().retryPolicy(RetryPolicy.none())
                        .apiCallTimeout(Duration.ofMinutes(15)).apiCallAttemptTimeout(Duration.ofMinutes(15)).build())
                .build()) {
            long t0 = System.nanoTime();
            SdkBytes payload = SdkBytes.fromString(State.JSON.writeValueAsString(
                    Map.of("prompt", req.prompt(), "actor_id", req.actor())), StandardCharsets.UTF_8);
            String body = rt.invokeAgentRuntime(b -> b.agentRuntimeArn(arn).runtimeSessionId(req.session())
                            .runtimeUserId(req.actor())         // lets the runtime mint a workload token for Identity
                            .contentType("application/json")
                            .payload(payload),
                    ResponseTransformer.toBytes()).asUtf8String();
            long secs = Math.round((System.nanoTime() - t0) / 1e9);
            System.out.println(render(req, body, secs));
        }
    }

    /** The same three lines + result invoke.py prints. */
    static String render(Request req, String body, long secs) throws Exception {
        JsonNode out = State.JSON.readTree(body);
        List<String> tools = new ArrayList<>();
        out.path("tools_used").forEach(t -> tools.add(t.asText()));
        String result = out.hasNonNull("result") ? out.get("result").asText() : out.toPrettyString();
        return "  agent    " + req.agent() + "   session " + req.session() + "   " + secs + "s\n"
             + "  tools    " + (tools.isEmpty() ? "(none)" : String.join(", ", tools)) + "   stop=" + out.path("stop_reason").asText("None") + "\n"
             + "\n" + result + "\n\n"
             + "  follow up:  java -jar java-tools/target/agentcore-tools.jar invoke " + req.agent() + " \"...\" --session " + req.session();
    }

}
