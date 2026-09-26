package com.airamatrix.capstone.aws;

import static com.airamatrix.capstone.aws.AwsEnv.say;

import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.bedrockagentcorecontrol.BedrockAgentCoreControlClient;
import software.amazon.awssdk.services.bedrockagentcorecontrol.model.ResourceNotFoundException;
import software.amazon.awssdk.services.cloudwatchlogs.CloudWatchLogsClient;
import software.amazon.awssdk.services.ecr.EcrClient;
import software.amazon.awssdk.services.ecr.model.RepositoryNotFoundException;
import software.amazon.awssdk.services.iam.IamClient;
import software.amazon.awssdk.services.iam.model.NoSuchEntityException;

/** Deletes exactly what capstone-state.json lists - the runtime, its role, the image repository, the runtime's log group. Never the shared stack. */
public final class Teardown {
    private Teardown() {}

    public static void run(AwsEnv env) throws Exception {
        JsonNode own = env.own();
        JsonNode rt = own.path("runtime");
        if (rt.hasNonNull("id")) {
            try (BedrockAgentCoreControlClient ac = BedrockAgentCoreControlClient.builder().region(env.awsRegion()).build()) {
                ac.deleteAgentRuntime(b -> b.agentRuntimeId(rt.get("id").asText()));
                say("runtime  deleted " + rt.path("name").asText());
                for (int i = 0; i < 30; i++) {
                    try { ac.getAgentRuntime(b -> b.agentRuntimeId(rt.get("id").asText())); Thread.sleep(5000); }
                    catch (ResourceNotFoundException gone) { break; }
                }
            } catch (ResourceNotFoundException e) { say("runtime  already gone"); }
            try (CloudWatchLogsClient logs = CloudWatchLogsClient.builder().region(env.awsRegion()).build()) {
                logs.deleteLogGroup(b -> b.logGroupName(rt.path("log_group").asText()));
                say("logs     deleted " + rt.path("log_group").asText());
            } catch (software.amazon.awssdk.services.cloudwatchlogs.model.ResourceNotFoundException e) { say("logs     no log group"); }
            env.saveOwn("runtime", null);
        }
        if (own.hasNonNull("role_name")) {
            String name = own.get("role_name").asText();
            try (IamClient iam = IamClient.builder().region(Region.AWS_GLOBAL).build()) {
                try { iam.deleteRolePolicy(b -> b.roleName(name).policyName("least-privilege")); } catch (NoSuchEntityException ignored) { }
                try { iam.deleteRole(b -> b.roleName(name)); say("iam      deleted role " + name); } catch (NoSuchEntityException e) { say("iam      role already gone"); }
            }
            env.saveOwn("role_name", null);
        }
        if (own.hasNonNull("ecr_repo")) {
            try (EcrClient ecr = EcrClient.builder().region(env.awsRegion()).build()) {
                ecr.deleteRepository(b -> b.repositoryName(own.get("ecr_repo").asText()).force(true));
                say("ecr      deleted " + own.get("ecr_repo").asText());
            } catch (RepositoryNotFoundException e) { say("ecr      already gone"); }
            env.saveOwn("ecr_repo", null);
            env.saveOwn("image", null);
        }
        say("done     nothing of the shared Day 4 stack was touched");
    }
}
