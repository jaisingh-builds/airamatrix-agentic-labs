package com.airamatrix.agentcore;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BiFunction;

import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;

/** One invocation: identity -> gateway tools (+ specialists, memory for the supervisor) -> agent loop. */
public class AgentService {
    private final Settings settings;
    private final AgentLoop loop;
    private final IdentityTokens identity;
    private final BiFunction<String, String, ToolBox> gateway;     // (gatewayUrl, bearer) -> tools
    private final MemoryStore memory;
    private final BedrockAgentCoreClient specialists;

    public AgentService(Settings settings, AgentLoop loop, IdentityTokens identity, BiFunction<String, String, ToolBox> gateway,
                        MemoryStore memory, BedrockAgentCoreClient specialists) {
        this.settings = settings;
        this.loop = loop;
        this.identity = identity;
        this.gateway = gateway;
        this.memory = memory;
        this.specialists = specialists;
    }

    public Map<String, Object> invoke(String prompt, String actor, String session, String workloadToken) {
        String role = settings.role();
        GenAiTelemetry tel = new GenAiTelemetry(session, role);
        String token = identity.gatewayToken(workloadToken);
        ToolBox gw = gateway.apply(settings.gatewayUrl(), token);
        ToolBox tools = settings.supervisor()
                ? ToolBox.of(gw, new SpecialistTools(specialists, settings.investigatorArn(), settings.reviewerArn(), session, actor))
                : gw;
        try (tools) {
            String system = Prompts.forRole(role);
            List<MemoryStore.Turn> history = List.of();
            if (memory != null) {
                history = memory.history(actor, session);
                List<String> facts = memory.facts(actor, prompt);
                if (!facts.isEmpty()) {
                    system += "\n\nWhat you remember from earlier sessions (long-term memory):\n- " + String.join("\n- ", facts);
                }
            }
            AgentLoop.Result r = loop.run(system, history, prompt, tools, tel);
            if (memory != null && !"guardrail_intervened".equals(r.stopReason()) && !r.text().isBlank()) {
                memory.save(actor, session, prompt, r.transcript());
            }
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("role", role);
            out.put("result", r.text());
            out.put("stop_reason", r.stopReason());
            out.put("tools_used", r.toolsUsed());
            return out;
        }
    }
}
