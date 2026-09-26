package com.airamatrix.capstone.aws;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import com.airamatrix.capstone.Store;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.core.client.config.ClientOverrideConfiguration;
import software.amazon.awssdk.core.retry.RetryPolicy;
import software.amazon.awssdk.core.sync.ResponseTransformer;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;

/**
 * InvokeAgentRuntime for the capstone runtime, then the SAME local bookkeeping as a local run: the run and its
 * proposal go into the capstone store (mode agentcore) and the returned span records into
 * <LAB_TRACE_DIR>/capstone-<run>.jsonl - so show / trace / approve work unchanged. A long read timeout and NO
 * automatic retry: an SDK retry after a timeout would start a second, parallel run.
 */
public final class Invoke {
    private Invoke() {}

    public static JsonNode call(AwsEnv env, String account, String asOf, String prompt, String actor, String runId) throws Exception {
        String arn = env.own().path("runtime").path("arn").asText(null);
        if (arn == null) throw new IllegalStateException("no runtime in " + env.ownPath().getFileName() + " - run deploy first");
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("prompt", prompt == null ? "" : prompt);
        payload.put("actor_id", actor);
        payload.put("account_id", account);
        payload.put("as_of", asOf);
        payload.put("run_id", runId);
        String session = "capstone-" + UUID.randomUUID().toString().replace("-", "");
        SdkBytes bytes = SdkBytes.fromString(Contracts.JSON.writeValueAsString(payload), StandardCharsets.UTF_8);
        try (BedrockAgentCoreClient rt = BedrockAgentCoreClient.builder().region(env.awsRegion())
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofMinutes(10)).build())
                .overrideConfiguration(ClientOverrideConfiguration.builder().retryPolicy(RetryPolicy.none())
                        .apiCallTimeout(Duration.ofMinutes(10)).apiCallAttemptTimeout(Duration.ofMinutes(10)).build()).build()) {
            String body = rt.invokeAgentRuntime(b -> b.agentRuntimeArn(arn).runtimeSessionId(session).runtimeUserId(actor)
                            .contentType("application/json").payload(bytes),
                    ResponseTransformer.toBytes()).asUtf8String();
            return Contracts.JSON.readTree(body);
        }
    }

    /** Store an AgentCore run like a local one and write its trace file. Returns the trace path. */
    public static Path record(Store store, JsonNode r, String question) throws Exception {
        String rid = r.path("run_id").asText();
        if (rid.isBlank()) throw new IllegalStateException("the runtime returned no run: " + Spans.redact(Contracts.JSON.writeValueAsString(r)));
        Spans tr = new Spans("capstone", rid);
        Files.createDirectories(tr.path.getParent());
        StringBuilder lines = new StringBuilder();
        for (JsonNode s : r.path("trace")) lines.append(s.toString()).append('\n');
        Files.writeString(tr.path, lines.toString(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        store.createRun(rid, r.path("account_id").asText(), r.path("as_of").asText(), question, "agentcore");
        store.finishRun(rid, r.path("status").asText(), r.path("cost_usd").asDouble(), r.path("turns").asInt(), r.path("tool_calls").asInt(),
                r.hasNonNull("error") ? r.get("error").asText() : null, tr.path.toString());
        if (r.hasNonNull("proposal")) {
            store.saveProposal(rid, r.get("proposal"), r.get("sla"), r.get("verdict"), r.get("trajectory"));
        }
        return tr.path;
    }

    /** The agentcore eval target: one invocation per case, graded exactly like local runs. */
    public static com.airamatrix.capstone.Evals.CaseRunner evalTarget(AwsEnv env) {
        return kase -> {
            long t0 = System.nanoTime();
            try {
                JsonNode r = call(env, kase.path("account").asText(), kase.path("as_of").asText(), kase.path("question").asText(""),
                        "capstone-eval", Store.newId());
                ObjectNode out = ((ObjectNode) r).deepCopy();
                out.remove("trace");
                out.remove("sla");
                out.put("seconds", Math.round((System.nanoTime() - t0) / 1e8) / 10.0);
                String st = r.path("status").asText();
                if (st.equals("failed") || st.equals("guardrail_intervened") || r.has("error") && !r.has("proposal")) {
                    return Contracts.object().put("error", r.path("error").asText(st)).put("status", st).put("cost_usd", r.path("cost_usd").asDouble());
                }
                return out;
            } catch (Exception e) {
                return Contracts.object().put("error", e.getClass().getSimpleName() + ": " + Spans.redact(String.valueOf(e.getMessage())))
                        .put("cost_usd", 0.0);
            }
        };
    }
}
