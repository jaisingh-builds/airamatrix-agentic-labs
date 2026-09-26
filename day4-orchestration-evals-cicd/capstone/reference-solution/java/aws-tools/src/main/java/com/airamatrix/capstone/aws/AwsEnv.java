package com.airamatrix.capstone.aws;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Arrays;
import java.util.List;

import com.airamatrix.capstone.Repo;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sts.StsClient;

/**
 * Settings and the two state files. AC_PREFIX / AWS_REGION / AC_MODEL_ID as for every AgentCore step.
 *   shared: agentcore/out/state.json (AC_STATE) - the Day 4 stack's guardrail, identity, gateway. READ ONLY here.
 *   own:    reference-solution/java/out/capstone-state.json (CAPSTONE_STATE) - what THIS tool created, for teardown.
 * Account ids, ARNs and ids live only in these gitignored files, never in the repo.
 */
public record AwsEnv(String region, String prefix, String account, String modelId, Path sharedPath, Path ownPath) {

    public static AwsEnv load() {
        String region = or("AWS_REGION", "ap-south-1");
        String account;
        try (StsClient sts = StsClient.builder().region(Region.of(region)).build()) { account = sts.getCallerIdentity().account(); }
        Path shared = Paths.get(or("AC_STATE", Repo.root().resolve("day4-orchestration-evals-cicd/agentcore/out/state.json").toString()));
        Path own = Paths.get(or("CAPSTONE_STATE", Repo.java().resolve("out/capstone-state.json").toString()));
        return new AwsEnv(region, or("AC_PREFIX", "aira-d4"), account, or("AC_MODEL_ID", "global.anthropic.claude-sonnet-5"), shared, own);
    }

    public Region awsRegion() { return Region.of(region); }
    public String prefixUnderscore() { return prefix.replace('-', '_'); }
    /** aira_d4cap_java_responder - distinct from the Python (aira_d4cap_py_*) and Node (aira_d4cap_node_*) runtimes. */
    public String runtimeName() { return prefixUnderscore() + "cap_java_responder"; }
    public String roleName() { return prefix + "-capstone-java-runtime"; }
    public String repoName() { return prefix + "-capstone-java"; }

    public JsonNode shared() {
        try {
            if (!Files.exists(sharedPath)) throw new IllegalStateException("no " + sharedPath + " - the Day 4 AgentCore stack (steps 02-05) must exist; set AC_STATE");
            return Contracts.JSON.readTree(sharedPath.toFile());
        } catch (IOException e) { throw new IllegalStateException(e); }
    }

    public JsonNode need(String dottedKey) {
        JsonNode n = shared();
        for (String k : dottedKey.split("\\.")) n = n.path(k);
        if (n.isMissingNode() || n.isNull() || (n.isTextual() && n.asText().isBlank())) {
            throw new IllegalStateException("agentcore/out/state.json has no '" + dottedKey + "' - run the AgentCore step that creates it");
        }
        return n;
    }

    public ObjectNode own() {
        try { return Files.exists(ownPath) ? (ObjectNode) Contracts.JSON.readTree(ownPath.toFile()) : Contracts.object(); }
        catch (IOException e) { throw new IllegalStateException(e); }
    }

    public void saveOwn(String key, JsonNode value) {
        try {
            ObjectNode o = own();
            if (value == null) o.remove(key); else o.set(key, value);
            Files.createDirectories(ownPath.getParent());
            Contracts.JSON.writerWithDefaultPrettyPrinter().writeValue(ownPath.toFile(), o);
            try { Files.setPosixFilePermissions(ownPath, PosixFilePermissions.fromString("rw-------")); } catch (UnsupportedOperationException ignored) { }
        } catch (IOException e) { throw new IllegalStateException(e); }
    }

    public List<String> investigatorScopes() {
        return Arrays.asList(Contracts.JSON.convertValue(need("clients.investigator.scopes"), String[].class));
    }

    static String or(String k, String d) {
        String v = System.getenv(k);
        return v == null || v.isBlank() ? d : v;
    }

    static void say(String s) { System.out.println("  " + s); System.out.flush(); }
}
