package com.airamatrix.agentcore;

import java.util.Map;
import java.util.TreeMap;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockruntime.BedrockRuntimeClient;

/** Real AWS wiring, from the environment the deploy step sets. Tests use the "test" profile and their own beans. */
@Configuration
@Profile("!test")
public class AgentWiring {

    @Bean
    Settings settings() {
        Settings s = Settings.fromEnv();
        // Startup line in the runtime log group: which role, and which telemetry settings the Runtime gave us.
        Map<String, String> otel = new TreeMap<>();
        System.getenv().forEach((k, v) -> { if (k.startsWith("OTEL_") || k.startsWith("AGENT_OBSERVABILITY")) otel.put(k, v); });
        System.out.println("aira-agents: role=" + s.role() + " model=" + s.modelId() + " guardrail=" + s.guardrailId()
                + " v" + s.guardrailVersion() + " otel=" + otel.keySet());
        return s;
    }

    @Bean
    AgentService agentService(Settings s) {
        BedrockRuntimeClient bedrock = Aws.bedrock(s.region());
        BedrockAgentCoreClient core = Aws.agentCore(s.region());
        return new AgentService(s,
                new AgentLoop(bedrock::converse, s.modelId(), s.guardrailId(), s.guardrailVersion()),
                new IdentityTokens(core, s.oauthProvider(), s.oauthScopes()),
                GatewayTools::new,
                s.supervisor() ? new MemoryStore(core, s.memoryId()) : null,
                s.supervisor() ? Aws.agentCoreLongCalls(s.region()) : null);
    }
}
