package com.airamatrix.agentcore;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.core.ResponseBytes;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.core.sync.ResponseTransformer;
import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockagentcore.model.InvokeAgentRuntimeRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.InvokeAgentRuntimeResponse;

/** The supervisor's two specialists - agent-to-agent calls to the other runtimes via InvokeAgentRuntime. */
public final class SpecialistTools implements ToolBox {
    private final BedrockAgentCoreClient client;
    private final String investigatorArn, reviewerArn, session, user;

    public SpecialistTools(BedrockAgentCoreClient client, String investigatorArn, String reviewerArn, String session, String user) {
        this.client = client;
        this.investigatorArn = investigatorArn;
        this.reviewerArn = reviewerArn;
        this.session = session == null ? UUID.randomUUID().toString() : session;
        this.user = user;
    }

    @Override
    public List<Spec> specs() {
        return List.of(
            new Spec("ask_investigator", "Ask the investigator agent to investigate a ticket and propose a change with evidence.",
                Map.of("type", "object", "required", List.of("ticket_id"), "properties", Map.of(
                    "ticket_id", Map.of("type", "string", "description", "e.g. T-1001"),
                    "question", Map.of("type", "string", "description", "optional extra instructions")))),
            new Spec("ask_reviewer", "Ask the reviewer agent for an independent verdict on a proposal (pass the proposal JSON and its evidence).",
                Map.of("type", "object", "required", List.of("proposal_and_evidence"), "properties", Map.of(
                    "proposal_and_evidence", Map.of("type", "string")))));
    }

    @Override
    public Outcome call(String name, Map<String, Object> input) {
        try {
            return switch (name) {
                case "ask_investigator" -> new Outcome(invoke(investigatorArn, "inv",
                        ("Investigate ticket " + input.get("ticket_id") + ". " + input.getOrDefault("question", "")).trim()), false);
                case "ask_reviewer" -> new Outcome(invoke(reviewerArn, "rev", String.valueOf(input.get("proposal_and_evidence"))), false);
                default -> new Outcome("{\"error\": \"unknown specialist " + name + "\"}", true);
            };
        } catch (Exception e) {
            return new Outcome("{\"error\": " + Json.write(e.getClass().getSimpleName() + ": " + e.getMessage()) + "}", true);
        }
    }

    String invoke(String arn, String suffix, String prompt) throws Exception {
        // runtimeSessionId must be at least 33 characters
        String sid = (session + "-" + suffix + "-" + "0".repeat(40)).substring(0, Math.max(40, session.length() + suffix.length() + 2));
        ResponseBytes<InvokeAgentRuntimeResponse> r = client.invokeAgentRuntime(InvokeAgentRuntimeRequest.builder()
                .agentRuntimeArn(arn)
                .runtimeSessionId(sid.length() > 100 ? sid.substring(0, 100) : sid)
                .runtimeUserId(user)
                .contentType("application/json")
                .payload(SdkBytes.fromString(Json.write(Map.of("prompt", prompt)), StandardCharsets.UTF_8))
                .build(), ResponseTransformer.toBytes());
        JsonNode out = Json.MAPPER.readTree(r.asUtf8String());
        return out.path("result").asText(out.toString());
    }
}
