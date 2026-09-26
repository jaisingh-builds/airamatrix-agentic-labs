package com.airamatrix.capstone.aws;

import static com.airamatrix.capstone.aws.AwsEnv.say;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.node.ObjectNode;

import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.bedrockagentcorecontrol.BedrockAgentCoreControlClient;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.AgentRuntime;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.AgentRuntimeArtifact;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.AgentRuntimeStatus;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.ContainerConfiguration;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.GetAgentRuntimeResponse;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.NetworkConfiguration;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.NetworkMode;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.ProtocolConfiguration;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.ServerProtocol;
import software.amazon.awssdk.services.ecr.EcrClient;
import software.amazon.awssdk.services.ecr.model.ImageTagMutability;
import software.amazon.awssdk.services.ecr.model.RepositoryNotFoundException;
import software.amazon.awssdk.services.iam.IamClient;
import software.amazon.awssdk.services.iam.model.NoSuchEntityException;

/**
 * ecr-repo and deploy: one ECR repository, one IAM role (least privilege: model, guardrail, the investigator's
 * Identity provider, logs/traces, pull THIS image), one runtime - reusing the shared stack's guardrail, identity
 * provider and gateway, never modifying them. Everything created is recorded in the capstone's own state file.
 */
public final class Deploy {
    private final AwsEnv env;

    public Deploy(AwsEnv env) { this.env = env; }

    public String ecrRepo() {
        try (EcrClient ecr = EcrClient.builder().region(env.awsRegion()).build()) {
            String uri;
            try {
                uri = ecr.describeRepositories(r -> r.repositoryNames(env.repoName())).repositories().get(0).repositoryUri();
                say("ecr      exists  " + env.repoName());
            } catch (RepositoryNotFoundException e) {
                uri = ecr.createRepository(r -> r.repositoryName(env.repoName()).imageTagMutability(ImageTagMutability.MUTABLE)
                        .imageScanningConfiguration(s -> s.scanOnPush(true))).repository().repositoryUri();
                say("ecr      created " + env.repoName());
            }
            ecr.putLifecyclePolicy(r -> r.repositoryName(env.repoName()).lifecyclePolicyText(
                    "{\"rules\":[{\"rulePriority\":1,\"description\":\"keep last 5\",\"selection\":{\"tagStatus\":\"any\","
                    + "\"countType\":\"imageCountMoreThan\",\"countNumber\":5},\"action\":{\"type\":\"expire\"}}]}"));
            env.saveOwn("ecr_repo", Contracts.JSON.getNodeFactory().textNode(env.repoName()));
            return uri;
        }
    }

    public void deploy(String image) throws Exception {
        if (!image.contains(".dkr.ecr.") || !image.contains("/" + env.repoName() + ":")) {
            throw new IllegalArgumentException("--image must be an image in this tool's repository " + env.repoName() + " (run ecr-repo first)");
        }
        Map<String, String> vars = environment();
        String roleArn;
        try (IamClient iam = IamClient.builder().region(Region.AWS_GLOBAL).build()) {
            roleArn = role(iam, env.roleName(), Contracts.JSON.writeValueAsString(trust(env.account())),
                    Contracts.JSON.writeValueAsString(policy(env)));
        }
        env.saveOwn("role_name", Contracts.JSON.getNodeFactory().textNode(env.roleName()));
        try (BedrockAgentCoreControlClient ac = BedrockAgentCoreControlClient.builder().region(env.awsRegion()).build()) {
            AgentRuntimeArtifact art = AgentRuntimeArtifact.fromContainerConfiguration(ContainerConfiguration.builder().containerUri(image).build());
            NetworkConfiguration net = NetworkConfiguration.builder().networkMode(NetworkMode.PUBLIC).build();
            ProtocolConfiguration proto = ProtocolConfiguration.builder().serverProtocol(ServerProtocol.HTTP).build();
            String desc = "Day 4 capstone reference: SLA-breach responder (Java container)";
            String id = find(ac, env.runtimeName());
            if (id != null) {
                final String rid = id;
                ac.updateAgentRuntime(r -> r.agentRuntimeId(rid).agentRuntimeArtifact(art).roleArn(roleArn).networkConfiguration(net)
                        .protocolConfiguration(proto).environmentVariables(vars).description(desc));
                say("runtime  updating " + env.runtimeName());
            } else {
                id = ac.createAgentRuntime(r -> r.agentRuntimeName(env.runtimeName()).agentRuntimeArtifact(art).roleArn(roleArn)
                        .networkConfiguration(net).protocolConfiguration(proto).environmentVariables(vars).description(desc)).agentRuntimeId();
                say("runtime  creating " + env.runtimeName());
            }
            ObjectNode rt = Contracts.object().put("id", id).put("name", env.runtimeName())
                    .put("log_group", "/aws/bedrock-agentcore/runtimes/" + id + "-DEFAULT");
            env.saveOwn("runtime", rt);                             // recorded before the wait: teardown works even if it fails
            GetAgentRuntimeResponse r = waitReady(ac, id);
            rt.put("arn", r.agentRuntimeArn());
            env.saveOwn("runtime", rt);
            env.saveOwn("image", Contracts.JSON.getNodeFactory().textNode(image));
            say("runtime  READY    " + env.runtimeName() + "  (arn in " + env.ownPath().getFileName() + ")");
        }
    }

    /** Exactly what RuntimeSettings.fromEnv() reads. */
    Map<String, String> environment() {
        Map<String, String> e = new LinkedHashMap<>();
        e.put("AWS_REGION", env.region());
        e.put("GATEWAY_URL", env.need("gateway_url").asText());
        e.put("OAUTH_PROVIDER", env.need("providers.investigator.name").asText());
        e.put("OAUTH_SCOPES", String.join(" ", env.investigatorScopes()));
        e.put("MODEL_ID", env.modelId());
        e.put("GUARDRAIL_ID", env.need("guardrail_id").asText());
        e.put("GUARDRAIL_VERSION", env.need("guardrail_version").asText());
        e.put("MAX_BUDGET_USD", AwsEnv.or("CAPSTONE_MAX_BUDGET_USD", "0.40"));
        e.put("MAX_TURNS", AwsEnv.or("CAPSTONE_MAX_TURNS", "10"));
        e.put("LAB_TRACE_DIR", "/tmp/traces");
        e.put("AGENT_OBSERVABILITY_ENABLED", "true");
        return e;
    }

    /** The runtime role's one inline policy. Pure, so it is tested offline. No gateway write, no Memory, no other runtime. */
    static Map<String, Object> policy(AwsEnv env) {
        String acct = env.account(), reg = env.region(), rt = "arn:aws:bedrock-agentcore:" + reg + ":" + acct;
        String providerArn = env.need("providers.investigator.arn").asText(), providerName = env.need("providers.investigator.name").asText();
        List<Map<String, Object>> s = new ArrayList<>();
        s.add(stmt("Model", List.of("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"),
                List.of("arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:::foundation-model/*", "arn:aws:bedrock:*:" + acct + ":inference-profile/*")));
        s.add(stmt("Guardrail", "bedrock:ApplyGuardrail", "arn:aws:bedrock:" + reg + ":" + acct + ":guardrail/" + env.need("guardrail_id").asText()));
        s.add(stmt("Identity", List.of("bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"),
                List.of(rt + ":workload-identity-directory/default", rt + ":workload-identity-directory/default/*", rt + ":token-vault/default", providerArn)));
        s.add(stmt("ProviderSecret", "secretsmanager:GetSecretValue", "arn:aws:secretsmanager:" + reg + ":" + acct
                + ":secret:bedrock-agentcore-identity!default/oauth2/" + providerName + "*"));
        s.add(stmt("Logs", List.of("logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                "logs:DescribeLogGroups"), "arn:aws:logs:" + reg + ":" + acct + ":log-group:*"));
        s.add(stmt("Traces", List.of("xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"), "*"));
        Map<String, Object> metrics = stmt("Metrics", "cloudwatch:PutMetricData", "*");
        metrics.put("Condition", Map.of("StringEquals", Map.of("cloudwatch:namespace", "bedrock-agentcore")));
        s.add(metrics);
        s.add(stmt("PullImage", List.of("ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"), "arn:aws:ecr:" + reg + ":" + acct + ":repository/" + env.repoName()));
        s.add(stmt("EcrAuth", "ecr:GetAuthorizationToken", "*"));
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("Version", "2012-10-17");
        doc.put("Statement", s);
        return doc;
    }

    static Map<String, Object> trust(String account) {
        return Map.of("Version", "2012-10-17", "Statement", List.of(Map.of("Effect", "Allow",
                "Principal", Map.of("Service", "bedrock-agentcore.amazonaws.com"), "Action", "sts:AssumeRole",
                "Condition", Map.of("StringEquals", Map.of("aws:SourceAccount", account)))));
    }

    static Map<String, Object> stmt(String sid, Object action, Object resource) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("Sid", sid);
        m.put("Effect", "Allow");
        m.put("Action", action);
        m.put("Resource", resource);
        return m;
    }

    static String role(IamClient iam, String name, String trust, String policy) throws InterruptedException {
        String arn;
        boolean fresh = false;
        try {
            arn = iam.getRole(r -> r.roleName(name)).role().arn();
            iam.updateAssumeRolePolicy(r -> r.roleName(name).policyDocument(trust));
        } catch (NoSuchEntityException e) {
            arn = iam.createRole(r -> r.roleName(name).assumeRolePolicyDocument(trust).description("Day 4 capstone reference runtime")).role().arn();
            fresh = true;
        }
        iam.putRolePolicy(r -> r.roleName(name).policyName("least-privilege").policyDocument(policy));
        say("iam      role " + name + (fresh ? " created" : " updated"));
        if (fresh) Thread.sleep(12_000);   // a new role takes a few seconds before a service can assume it
        return arn;
    }

    static String find(BedrockAgentCoreControlClient ac, String name) {
        for (AgentRuntime r : ac.listAgentRuntimesPaginator(b -> b.maxResults(100)).agentRuntimes()) {
            if (r.agentRuntimeName().equals(name)) return r.agentRuntimeId();
        }
        return null;
    }

    static GetAgentRuntimeResponse waitReady(BedrockAgentCoreControlClient ac, String id) throws InterruptedException {
        for (int i = 0; i < 90; i++) {
            GetAgentRuntimeResponse r = ac.getAgentRuntime(b -> b.agentRuntimeId(id));
            if (r.status() == AgentRuntimeStatus.READY) return r;
            if (r.status() == AgentRuntimeStatus.CREATE_FAILED || r.status() == AgentRuntimeStatus.UPDATE_FAILED) {
                throw new IllegalStateException("runtime " + r.status() + ": " + r.failureReason());
            }
            Thread.sleep(10_000);
        }
        throw new IllegalStateException("runtime still not ready");
    }
}
