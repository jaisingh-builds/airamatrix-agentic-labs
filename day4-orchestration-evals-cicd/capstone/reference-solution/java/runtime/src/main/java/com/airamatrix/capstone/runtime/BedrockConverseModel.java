package com.airamatrix.capstone.runtime;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.ModelClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.context.Scope;
import software.amazon.awssdk.services.bedrockruntime.model.ContentBlock;
import software.amazon.awssdk.services.bedrockruntime.model.ConversationRole;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseRequest;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseResponse;
import software.amazon.awssdk.services.bedrockruntime.model.GuardrailConfiguration;
import software.amazon.awssdk.services.bedrockruntime.model.GuardrailTrace;
import software.amazon.awssdk.services.bedrockruntime.model.InferenceConfiguration;
import software.amazon.awssdk.services.bedrockruntime.model.Message;
import software.amazon.awssdk.services.bedrockruntime.model.SystemContentBlock;
import software.amazon.awssdk.services.bedrockruntime.model.Tool;
import software.amazon.awssdk.services.bedrockruntime.model.ToolConfiguration;
import software.amazon.awssdk.services.bedrockruntime.model.ToolInputSchema;
import software.amazon.awssdk.services.bedrockruntime.model.ToolResultBlock;
import software.amazon.awssdk.services.bedrockruntime.model.ToolResultContentBlock;
import software.amazon.awssdk.services.bedrockruntime.model.ToolResultStatus;
import software.amazon.awssdk.services.bedrockruntime.model.ToolSpecification;
import software.amazon.awssdk.services.bedrockruntime.model.ToolUseBlock;

/**
 * common's {@link ModelClient} on Bedrock Converse: the agent loop speaks the Messages API shape in both modes,
 * and this class translates. The Bedrock Guardrail is on EVERY call (guardrailConfig) - a prompt change cannot
 * remove it; an intervention comes back as stop_reason "guardrail_intervened" and the loop ends the run.
 */
public final class BedrockConverseModel implements ModelClient {
    private final Function<ConverseRequest, ConverseResponse> converse;
    private final String modelId, guardrailId, guardrailVersion;
    private final GenAiTelemetry tel;

    public BedrockConverseModel(Function<ConverseRequest, ConverseResponse> converse, String modelId, String guardrailId,
                                String guardrailVersion, GenAiTelemetry tel) {
        this.converse = converse;
        this.modelId = modelId;
        this.guardrailId = guardrailId;
        this.guardrailVersion = guardrailVersion;
        this.tel = tel;
    }

    @Override
    public JsonNode messages(List<?> messages, List<?> tools, String system, int maxTokens) {
        ConverseRequest.Builder req = ConverseRequest.builder().modelId(modelId)
                .system(SystemContentBlock.fromText(system))
                .messages(toConverse(messages))
                .inferenceConfig(InferenceConfiguration.builder().maxTokens(maxTokens).build())
                .guardrailConfig(GuardrailConfiguration.builder().guardrailIdentifier(guardrailId)
                        .guardrailVersion(guardrailVersion).trace(GuardrailTrace.ENABLED).build());
        List<Tool> specs = new ArrayList<>();
        for (Object t : tools) {
            JsonNode n = Contracts.JSON.valueToTree(t);
            specs.add(Tool.fromToolSpec(ToolSpecification.builder().name(n.path("name").asText())
                    .description(n.path("description").asText())
                    .inputSchema(ToolInputSchema.fromJson(DocJson.toDocument(n.path("input_schema")))).build()));
        }
        if (!specs.isEmpty()) req.toolConfig(ToolConfiguration.builder().tools(specs).build());
        Span chat = tel == null ? Span.getInvalid() : tel.chat(modelId, system, Contracts.JSON.valueToTree(messages).toString());
        try (Scope ignored = chat.makeCurrent()) {
            ConverseResponse resp = converse.apply(req.build());
            JsonNode out = fromConverse(resp);
            chat.setAttribute("gen_ai.output.messages", out.path("content").toString());
            chat.setAttribute("gen_ai.response.finish_reasons", out.path("stop_reason").asText());
            chat.setAttribute("gen_ai.usage.input_tokens", out.path("usage").path("input_tokens").asLong());
            chat.setAttribute("gen_ai.usage.output_tokens", out.path("usage").path("output_tokens").asLong());
            return out;
        } catch (RuntimeException e) {
            GenAiTelemetry.fail(chat, e);
            throw e;
        } finally {
            chat.end();
        }
    }

    /** Messages API messages -> Converse messages. */
    static List<Message> toConverse(List<?> messages) {
        List<Message> out = new ArrayList<>();
        for (Object m : messages) {
            JsonNode n = Contracts.JSON.valueToTree(m);
            ConversationRole role = n.path("role").asText().equals("assistant") ? ConversationRole.ASSISTANT : ConversationRole.USER;
            List<ContentBlock> blocks = new ArrayList<>();
            JsonNode content = n.path("content");
            if (content.isTextual()) {
                blocks.add(ContentBlock.fromText(content.asText()));
            } else {
                for (JsonNode b : content) {
                    switch (b.path("type").asText()) {
                        case "text" -> { if (!b.path("text").asText().isBlank()) blocks.add(ContentBlock.fromText(b.path("text").asText())); }
                        case "tool_use" -> blocks.add(ContentBlock.fromToolUse(ToolUseBlock.builder().toolUseId(b.path("id").asText())
                                .name(b.path("name").asText()).input(DocJson.toDocument(b.path("input"))).build()));
                        case "tool_result" -> blocks.add(ContentBlock.fromToolResult(ToolResultBlock.builder()
                                .toolUseId(b.path("tool_use_id").asText())
                                .content(ToolResultContentBlock.fromText(b.path("content").asText("(empty)")))
                                .status(b.path("is_error").asBoolean(false) ? ToolResultStatus.ERROR : ToolResultStatus.SUCCESS).build()));
                        default -> { }
                    }
                }
            }
            if (blocks.isEmpty()) blocks.add(ContentBlock.fromText("(empty)"));
            out.add(Message.builder().role(role).content(blocks).build());
        }
        return out;
    }

    /** Converse response -> a Messages API response: content, stop_reason, usage. */
    static JsonNode fromConverse(ConverseResponse resp) {
        ObjectNode out = Contracts.object();
        ArrayNode content = out.putArray("content");
        Message msg = resp.output() == null ? null : resp.output().message();
        if (msg != null) {
            for (ContentBlock b : msg.content()) {
                if (b.text() != null) content.addObject().put("type", "text").put("text", b.text());
                else if (b.toolUse() != null) {
                    content.addObject().put("type", "tool_use").put("id", b.toolUse().toolUseId()).put("name", b.toolUse().name())
                            .set("input", DocJson.toJson(b.toolUse().input()));
                }
            }
        }
        out.put("stop_reason", resp.stopReasonAsString());
        ObjectNode u = out.putObject("usage");
        if (resp.usage() != null) {
            u.put("input_tokens", resp.usage().inputTokens()).put("output_tokens", resp.usage().outputTokens());
        }
        return out;
    }
}
