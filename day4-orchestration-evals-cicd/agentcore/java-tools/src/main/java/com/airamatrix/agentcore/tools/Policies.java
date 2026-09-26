package com.airamatrix.agentcore.tools;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The IAM policy for one Java agent - 06-agents/deploy_agents.py policy_for(), statement for statement,
 * plus ECR pull (a container runtime pulls its image with the agent's own role). Pure, so it is tested offline.
 */
public final class Policies {
    private Policies() {}

    public record Inputs(String region, String account, String prefixUnderscore, String guardrailId,
                         String providerArn, String providerName, String memoryArn, String repoName) {}

    public static Map<String, Object> agentPolicy(String role, Inputs in) {
        String rt = "arn:aws:bedrock-agentcore:" + in.region() + ":" + in.account();
        List<Map<String, Object>> s = new ArrayList<>();
        s.add(stmt("Model", List.of("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"),
                List.of("arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:::foundation-model/*",
                        "arn:aws:bedrock:*:" + in.account() + ":inference-profile/*")));
        s.add(stmt("Guardrail", "bedrock:ApplyGuardrail",
                "arn:aws:bedrock:" + in.region() + ":" + in.account() + ":guardrail/" + in.guardrailId()));
        s.add(stmt("Identity", List.of("bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"),
                List.of(rt + ":workload-identity-directory/default", rt + ":workload-identity-directory/default/*",
                        rt + ":token-vault/default", in.providerArn())));
        s.add(stmt("ProviderSecret", "secretsmanager:GetSecretValue", "arn:aws:secretsmanager:" + in.region() + ":" + in.account()
                + ":secret:bedrock-agentcore-identity!default/oauth2/" + in.providerName() + "*"));
        s.add(stmt("Logs", List.of("logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                "logs:DescribeLogStreams", "logs:DescribeLogGroups"), "arn:aws:logs:" + in.region() + ":" + in.account() + ":log-group:*"));
        s.add(stmt("Traces", List.of("xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules",
                "xray:GetSamplingTargets"), "*"));
        Map<String, Object> metrics = stmt("Metrics", "cloudwatch:PutMetricData", "*");
        metrics.put("Condition", Map.of("StringEquals", Map.of("cloudwatch:namespace", "bedrock-agentcore")));
        s.add(metrics);
        // container-only: pull the agent image
        s.add(stmt("PullImage", List.of("ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"),
                "arn:aws:ecr:" + in.region() + ":" + in.account() + ":repository/" + in.repoName()));
        s.add(stmt("EcrAuth", "ecr:GetAuthorizationToken", "*"));
        if (role.equals("supervisor")) {
            // only the JAVA specialists - {prefix_}j_* - never the Python ones
            s.add(stmt("CallSpecialists", List.of("bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"),
                    List.of(rt + ":runtime/" + in.prefixUnderscore() + "j_investigator-*",
                            rt + ":runtime/" + in.prefixUnderscore() + "j_reviewer-*")));
            s.add(stmt("Memory", List.of("bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListSessions", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:GetMemory", "bedrock-agentcore:DeleteEvent"), in.memoryArn()));
        }
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("Version", "2012-10-17");
        doc.put("Statement", s);
        return doc;
    }

    /** Trust: only the AgentCore service, only from this account (confused-deputy guard). */
    public static Map<String, Object> trust(String account) {
        return Map.of("Version", "2012-10-17", "Statement", List.of(Map.of(
                "Effect", "Allow", "Principal", Map.of("Service", "bedrock-agentcore.amazonaws.com"),
                "Action", "sts:AssumeRole", "Condition", Map.of("StringEquals", Map.of("aws:SourceAccount", account)))));
    }

    static Map<String, Object> stmt(String sid, Object action, Object resource) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("Sid", sid);
        m.put("Effect", "Allow");
        m.put("Action", action);
        m.put("Resource", resource);
        return m;
    }
}
