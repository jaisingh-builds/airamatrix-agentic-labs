package com.airamatrix.agentcore.tools;

import static com.airamatrix.agentcore.tools.Env.say;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
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
 * Step 6 in Java: the counterpart of 06-agents/deploy_agents.py for the container image.
 *
 *   ecr-repo              create the ECR repository the image is pushed to (once)
 *   deploy --image <uri>  one IAM role per agent, then create/update the three runtimes (specialists first:
 *                         the supervisor needs their ARNs), wait for READY, record java_runtimes in out/state.json
 */
public final class Deploy {
    static final List<String> SPECIALISTS = List.of("investigator", "reviewer");

    private final Env env;
    private final State state;

    public Deploy(Env env, State state) {
        this.env = env;
        this.state = state;
    }

    public String ecrRepo() throws Exception {
        try (EcrClient ecr = EcrClient.builder().region(env.awsRegion()).build()) {
            String uri;
            try {
                uri = ecr.describeRepositories(r -> r.repositoryNames(env.repoName())).repositories().get(0).repositoryUri();
                say("ecr      exists  " + uri);
            } catch (RepositoryNotFoundException e) {
                uri = ecr.createRepository(r -> r.repositoryName(env.repoName())
                        .imageTagMutability(ImageTagMutability.MUTABLE)
                        .imageScanningConfiguration(s -> s.scanOnPush(true))).repository().repositoryUri();
                say("ecr      created " + uri);
            }
            // keep the last 10 images - every build pushes a new tag
            ecr.putLifecyclePolicy(r -> r.repositoryName(env.repoName()).lifecyclePolicyText(
                    "{\"rules\":[{\"rulePriority\":1,\"description\":\"keep last 10\",\"selection\":{\"tagStatus\":\"any\","
                    + "\"countType\":\"imageCountMoreThan\",\"countNumber\":10},\"action\":{\"type\":\"expire\"}}]}"));
            state.save("java_ecr_repo", State.JSON.getNodeFactory().textNode(env.repoName()));
            return uri;
        }
    }

    public Map<String, String> deploy(String image) throws Exception {
        List<String> missing = state.missing("gateway_url", "providers", "clients", "guardrail_id", "guardrail_version",
                "memory_arn", "memory_id");
        if (!missing.isEmpty()) {
            throw new IllegalStateException("out/state.json has no " + missing + " - run steps 02-05 first");
        }
        if (!image.contains(".dkr.ecr.")) throw new IllegalArgumentException("--image must be an ECR image URI, got " + image);
        try (IamClient iam = IamClient.builder().region(Region.AWS_GLOBAL).build();
             BedrockAgentCoreControlClient ac = BedrockAgentCoreControlClient.builder().region(env.awsRegion()).build()) {
            Map<String, String> arns = new LinkedHashMap<>();
            for (String role : SPECIALISTS) arns.put(role, deployOne(iam, ac, role, image, Map.of()));
            arns.put("supervisor", deployOne(iam, ac, "supervisor", image, Map.of(
                    "INVESTIGATOR_ARN", arns.get("investigator"), "REVIEWER_ARN", arns.get("reviewer"),
                    "MEMORY_ID", state.need("memory_id").asText())));
            ObjectNode rt = state.object();
            arns.forEach(rt::put);
            state.save("java_runtimes", rt);
            state.save("java_image", State.JSON.getNodeFactory().textNode(image));
            say("saved    java_runtimes -> " + String.join(", ", arns.keySet()));
            return arns;
        }
    }

    /** The environment each runtime gets - exactly what Settings.fromEnv() in 06-agents-java reads. */
    Map<String, String> environment(String role, Map<String, String> extra) {
        JsonNode clients = state.need("clients"), providers = state.need("providers");
        List<String> scopes = State.JSON.convertValue(clients.path(role).path("scopes"),
                State.JSON.getTypeFactory().constructCollectionType(List.class, String.class));
        Map<String, String> e = new LinkedHashMap<>();
        e.put("ROLE", role);
        e.put("AWS_REGION", env.region());
        e.put("GATEWAY_URL", state.need("gateway_url").asText());
        e.put("OAUTH_PROVIDER", providers.path(role).path("name").asText());
        e.put("OAUTH_SCOPES", String.join(" ", scopes));
        e.put("MODEL_ID", env.modelId());
        e.put("GUARDRAIL_ID", state.need("guardrail_id").asText());
        e.put("GUARDRAIL_VERSION", state.need("guardrail_version").asText());
        e.put("AGENT_OBSERVABILITY_ENABLED", "true");
        e.putAll(extra);
        return e;
    }

    Policies.Inputs policyInputs(String role) {
        JsonNode p = state.need("providers").path(role);
        return new Policies.Inputs(env.region(), env.account(), env.prefixUnderscore(), state.need("guardrail_id").asText(),
                p.path("arn").asText(), p.path("name").asText(), state.need("memory_arn").asText(), env.repoName());
    }

    private String deployOne(IamClient iam, BedrockAgentCoreControlClient ac, String role, String image,
                             Map<String, String> extra) throws Exception {
        String roleArn = iamRole(iam, env.prefix() + "-java-agent-" + role,
                State.JSON.writeValueAsString(Policies.trust(env.account())),
                State.JSON.writeValueAsString(Policies.agentPolicy(role, policyInputs(role))));
        AgentRuntimeArtifact artifact = AgentRuntimeArtifact.fromContainerConfiguration(
                ContainerConfiguration.builder().containerUri(image).build());
        NetworkConfiguration net = NetworkConfiguration.builder().networkMode(NetworkMode.PUBLIC).build();
        ProtocolConfiguration proto = ProtocolConfiguration.builder().serverProtocol(ServerProtocol.HTTP).build();
        Map<String, String> vars = environment(role, extra);
        String name = env.runtimeName(role), description = "Day 4 " + role + " agent (Java container)";

        String id = findRuntime(ac, name);
        if (id != null) {
            final String rid = id;
            ac.updateAgentRuntime(r -> r.agentRuntimeId(rid).agentRuntimeArtifact(artifact).roleArn(roleArn)
                    .networkConfiguration(net).protocolConfiguration(proto).environmentVariables(vars).description(description));
            say(String.format("runtime  %-12s updating %s", role, id));
        } else {
            id = ac.createAgentRuntime(r -> r.agentRuntimeName(name).agentRuntimeArtifact(artifact).roleArn(roleArn)
                    .networkConfiguration(net).protocolConfiguration(proto).environmentVariables(vars)
                    .description(description)).agentRuntimeId();
            say(String.format("runtime  %-12s creating %s", role, id));
        }
        GetAgentRuntimeResponse r = waitReady(ac, id, role);
        say(String.format("runtime  %-12s READY  %s", role, r.agentRuntimeArn()));
        return r.agentRuntimeArn();
    }

    static String findRuntime(BedrockAgentCoreControlClient ac, String name) {
        for (AgentRuntime r : ac.listAgentRuntimesPaginator(b -> b.maxResults(100)).agentRuntimes()) {
            if (r.agentRuntimeName().equals(name)) return r.agentRuntimeId();
        }
        return null;
    }

    static GetAgentRuntimeResponse waitReady(BedrockAgentCoreControlClient ac, String id, String what) throws InterruptedException {
        for (int i = 0; i < 90; i++) {
            GetAgentRuntimeResponse r = ac.getAgentRuntime(b -> b.agentRuntimeId(id));
            AgentRuntimeStatus st = r.status();
            if (st == AgentRuntimeStatus.READY) return r;
            if (st == AgentRuntimeStatus.CREATE_FAILED || st == AgentRuntimeStatus.UPDATE_FAILED) {
                throw new IllegalStateException("runtime " + what + " " + st + ": " + r.failureReason());
            }
            Thread.sleep(10_000);
        }
        throw new IllegalStateException("runtime " + what + " still not ready");
    }

    /** common.role(): create or update a role trusted by one service, with one inline policy. */
    static String iamRole(IamClient iam, String name, String trust, String policy) throws InterruptedException {
        String arn;
        boolean fresh = false;
        try {
            arn = iam.getRole(r -> r.roleName(name)).role().arn();
            iam.updateAssumeRolePolicy(r -> r.roleName(name).policyDocument(trust));
        } catch (NoSuchEntityException e) {
            arn = iam.createRole(r -> r.roleName(name).assumeRolePolicyDocument(trust)
                    .description("AgentCore Day 4 Java agent")).role().arn();
            fresh = true;
        }
        iam.putRolePolicy(r -> r.roleName(name).policyName("least-privilege").policyDocument(policy));
        if (fresh) Thread.sleep(12_000);   // a brand-new role takes a few seconds before services can assume it
        return arn;
    }
}
