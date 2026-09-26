package com.airamatrix.agentcore.tools;

import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sts.StsClient;

/** The same settings as common.py: AC_PREFIX, AWS_REGION, AC_MODEL_ID. Nothing here is secret. */
public record Env(String region, String prefix, String account, String modelId) {

    public static Env load() {
        String region = or("AWS_REGION", "ap-south-1");
        String account;
        try (StsClient sts = StsClient.builder().region(Region.of(region)).build()) {
            account = sts.getCallerIdentity().account();
        }
        return new Env(region, or("AC_PREFIX", "aira-d4"), account, or("AC_MODEL_ID", "global.anthropic.claude-sonnet-5"));
    }

    /** aira-d4 -> aira_d4 (runtime names allow no dashes). */
    public String prefixUnderscore() { return prefix.replace('-', '_'); }

    /** Java runtimes are named {prefix_}j_{role} (aira_d4j_investigator) - next to the Python ones, no clash. */
    public String runtimeName(String role) { return prefixUnderscore() + "j_" + role; }

    public String repoName() { return prefix + "-agents-java"; }

    public Region awsRegion() { return Region.of(region); }

    static String or(String k, String d) {
        String v = System.getenv(k);
        return v == null || v.isBlank() ? d : v;
    }

    static void say(Object... parts) {
        StringBuilder sb = new StringBuilder("  ");
        for (int i = 0; i < parts.length; i++) sb.append(i == 0 ? "" : " ").append(parts[i]);
        System.out.println(sb);
        System.out.flush();
    }
}
