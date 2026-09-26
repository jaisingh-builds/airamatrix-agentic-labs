package com.airamatrix.capstone.runtime;

import java.time.Duration;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockruntime.BedrockRuntimeClient;

/** Real AWS wiring from the environment the deploy step sets. Credentials: the runtime's IAM role. Tests use profile "test". */
@Configuration
@Profile("!test")
public class RuntimeWiring {

    @Bean
    RuntimeSettings settings() {
        RuntimeSettings s = RuntimeSettings.fromEnv();
        System.out.println("aira-capstone: model=" + s.modelId() + " guardrail=" + s.guardrailId() + " v" + s.guardrailVersion()
                + " provider=" + s.oauthProvider() + " budget=$" + s.maxBudgetUsd() + " turns=" + s.maxTurns());
        return s;
    }

    @Bean
    InvocationService invocationService(RuntimeSettings s) {
        BedrockRuntimeClient bedrock = BedrockRuntimeClient.builder().region(Region.of(s.region()))
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofMinutes(3)).build()).build();
        BedrockAgentCoreClient core = BedrockAgentCoreClient.builder().region(Region.of(s.region()))
                .httpClient(ApacheHttpClient.builder().socketTimeout(Duration.ofSeconds(60)).build()).build();
        return new InvocationService(s, new IdentityTokens(core, s.oauthProvider(), s.oauthScopes()),
                (url, token) -> {
                    GatewayOpsReader g = new GatewayOpsReader(url, token);
                    return new InvocationService.Reads() {
                        @Override public com.fasterxml.jackson.databind.JsonNode account(String id) { return g.account(id); }
                        @Override public com.fasterxml.jackson.databind.JsonNode tickets(String id) { return g.tickets(id); }
                        @Override public com.fasterxml.jackson.databind.JsonNode ticket(String id) { return g.ticket(id); }
                        @Override public com.fasterxml.jackson.databind.JsonNode jobs(String id) { return g.jobs(id); }
                        @Override public com.fasterxml.jackson.databind.JsonNode config(String k) { return g.config(k); }
                        @Override public void close() { g.close(); }
                    };
                },
                tel -> new BedrockConverseModel(bedrock::converse, s.modelId(), s.guardrailId(), s.guardrailVersion(), tel));
    }
}
