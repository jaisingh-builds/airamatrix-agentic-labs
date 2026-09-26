package com.airamatrix.agentcore;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/** Everything the agent is configured with - environment variables set by the deploy step, never secrets. */
public record Settings(String role, String region, String gatewayUrl, String oauthProvider, List<String> oauthScopes,
                       String modelId, String guardrailId, String guardrailVersion, String memoryId,
                       String investigatorArn, String reviewerArn) {

    public static Settings fromEnv() { return from(System::getenv); }

    public static Settings from(Function<String, String> env) {
        String role = need(env, "ROLE");
        if (!List.of("investigator", "reviewer", "supervisor").contains(role)) {
            throw new IllegalStateException("ROLE must be investigator, reviewer or supervisor, not " + role);
        }
        boolean sup = role.equals("supervisor");
        return new Settings(role,
                or(env, "AWS_REGION", "ap-south-1"),
                need(env, "GATEWAY_URL"),
                need(env, "OAUTH_PROVIDER"),
                Arrays.asList(need(env, "OAUTH_SCOPES").trim().split("\\s+")),
                need(env, "MODEL_ID"),
                need(env, "GUARDRAIL_ID"),
                need(env, "GUARDRAIL_VERSION"),
                sup ? need(env, "MEMORY_ID") : env.apply("MEMORY_ID"),
                sup ? need(env, "INVESTIGATOR_ARN") : null,
                sup ? need(env, "REVIEWER_ARN") : null);
    }

    public static Settings of(Map<String, String> env) { return from(env::get); }

    public boolean supervisor() { return role.equals("supervisor"); }

    private static String need(Function<String, String> env, String k) {
        String v = env.apply(k);
        if (v == null || v.isBlank()) throw new IllegalStateException(k + " is not set - the deploy step sets it");
        return v;
    }

    private static String or(Function<String, String> env, String k, String d) {
        String v = env.apply(k);
        return v == null || v.isBlank() ? d : v;
    }
}
