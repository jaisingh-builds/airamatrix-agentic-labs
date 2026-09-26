package com.airamatrix.capstone.runtime;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/** What the runtime is configured with - environment variables the deploy step sets, never secrets. */
public record RuntimeSettings(String region, String gatewayUrl, String oauthProvider, List<String> oauthScopes, String modelId,
                              String guardrailId, String guardrailVersion, double maxBudgetUsd, int maxTurns) {

    public static RuntimeSettings fromEnv() { return from(System::getenv); }

    public static RuntimeSettings of(Map<String, String> env) { return from(env::get); }

    static RuntimeSettings from(Function<String, String> env) {
        return new RuntimeSettings(or(env, "AWS_REGION", "ap-south-1"), need(env, "GATEWAY_URL"), need(env, "OAUTH_PROVIDER"),
                Arrays.asList(need(env, "OAUTH_SCOPES").trim().split("\\s+")), need(env, "MODEL_ID"),
                need(env, "GUARDRAIL_ID"), need(env, "GUARDRAIL_VERSION"),
                Double.parseDouble(or(env, "MAX_BUDGET_USD", "0.40")), Integer.parseInt(or(env, "MAX_TURNS", "10")));
    }

    static String need(Function<String, String> env, String k) {
        String v = env.apply(k);
        if (v == null || v.isBlank()) throw new IllegalStateException(k + " is not set - the deploy step sets it");
        return v;
    }

    static String or(Function<String, String> env, String k, String d) {
        String v = env.apply(k);
        return v == null || v.isBlank() ? d : v;
    }
}
