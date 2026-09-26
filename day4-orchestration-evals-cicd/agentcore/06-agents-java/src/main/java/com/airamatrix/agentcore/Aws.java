package com.airamatrix.agentcore;

import java.time.Duration;

import software.amazon.awssdk.core.client.config.ClientOverrideConfiguration;
import software.amazon.awssdk.core.retry.RetryPolicy;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockruntime.BedrockRuntimeClient;

/** AWS SDK clients. Credentials: the runtime's IAM role, through the default provider chain. */
public final class Aws {
    private Aws() {}

    public static BedrockRuntimeClient bedrock(String region) {
        return BedrockRuntimeClient.builder().region(Region.of(region))
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofMinutes(3)).build()).build();
    }

    public static BedrockAgentCoreClient agentCore(String region) {
        return BedrockAgentCoreClient.builder().region(Region.of(region))
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofSeconds(60)).build()).build();
    }

    /**
     * For agent-to-agent calls. A specialist can take minutes, and an SDK retry after a read timeout
     * would run it a SECOND time (the Python build posted two identical comments that way):
     * long socket timeout, no retries.
     */
    public static BedrockAgentCoreClient agentCoreLongCalls(String region) {
        return BedrockAgentCoreClient.builder().region(Region.of(region))
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofMinutes(15)).build())
                .overrideConfiguration(ClientOverrideConfiguration.builder()
                        .retryPolicy(RetryPolicy.none())
                        .apiCallTimeout(Duration.ofMinutes(15)).apiCallAttemptTimeout(Duration.ofMinutes(15)).build())
                .build();
    }
}
