package com.airamatrix.agentcore;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

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
import software.amazon.awssdk.services.bedrockruntime.model.StopReason;
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
 * One agent run: the Bedrock Converse tool loop. What the Strands Agent + BedrockModel did for the
 * Python agent, in plain code:
 *   - the guardrail is on EVERY model call (guardrailConfig), so a prompt change cannot remove it
 *   - tools are whatever the ToolBox offers (Gateway tools filtered by Cedar, plus specialists)
 *   - the loop ends at end_turn, at guardrail_intervened, or at MAX_TURNS
 */
public class AgentLoop {
    public static final int MAX_TURNS = 20;
    static final int MAX_TOKENS = 4000;

    /** One Converse call. BedrockRuntimeClient::converse fits; tests pass a scripted fake. */
    public interface Model extends Function<ConverseRequest, ConverseResponse> {}

    /** transcript = what this run added to the conversation (assistant text, tool results) - saved to Memory. */
    public record Result(String text, String stopReason, List<String> toolsUsed, int turns, long inputTokens, long outputTokens,
                         List<MemoryStore.Turn> transcript) {}

    /** A tool result kept in the transcript is bounded; a Memory event's text limit is 100 000 characters. */
    static final int MAX_TOOL_TEXT = 8000;

    private final Model model;
    private final String modelId, guardrailId, guardrailVersion;

    public AgentLoop(Model model, String modelId, String guardrailId, String guardrailVersion) {
        this.model = model;
        this.modelId = modelId;
        this.guardrailId = guardrailId;
        this.guardrailVersion = guardrailVersion;
    }

    public Result run(String system, List<MemoryStore.Turn> history, String prompt, ToolBox tools, GenAiTelemetry tel) {
        List<Message> messages = new ArrayList<>();
        for (MemoryStore.Turn t : history) {
            messages.add(Message.builder().role(t.role().equals("assistant") ? ConversationRole.ASSISTANT : ConversationRole.USER)
                    .content(ContentBlock.fromText(t.text())).build());
        }
        messages = alternating(messages);
        messages.add(Message.builder().role(ConversationRole.USER).content(ContentBlock.fromText(prompt)).build());

        List<Tool> toolSpecs = tools.specs().stream().map(s -> Tool.fromToolSpec(ToolSpecification.builder()
                .name(s.name()).description(s.description())
                .inputSchema(ToolInputSchema.fromJson(Json.toDocument(s.inputSchema()))).build())).toList();

        List<String> used = new ArrayList<>();
        List<MemoryStore.Turn> transcript = new ArrayList<>();
        long in = 0, out = 0;
        String lastText = "";
        String stop = "max_turns";
        Span agentSpan = tel.agent(prompt);
        try (Scope ignored = GenAiTelemetry.activate(agentSpan)) {
            for (int turn = 1; turn <= MAX_TURNS; turn++) {
                ConverseRequest.Builder req = ConverseRequest.builder().modelId(modelId)
                        .system(SystemContentBlock.fromText(system))
                        .messages(messages)
                        .inferenceConfig(InferenceConfiguration.builder().maxTokens(MAX_TOKENS).build())
                        .guardrailConfig(GuardrailConfiguration.builder().guardrailIdentifier(guardrailId)
                                .guardrailVersion(guardrailVersion).trace(GuardrailTrace.ENABLED).build());
                if (!toolSpecs.isEmpty()) req.toolConfig(ToolConfiguration.builder().tools(toolSpecs).build());

                ConverseResponse resp;
                Span chat = tel.chat(modelId, system, messagesJson(messages));
                try (Scope s = GenAiTelemetry.activate(chat)) {
                    resp = model.apply(req.build());
                    Message reply = resp.output().message();
                    chat.setAttribute("gen_ai.output.messages", messagesJson(List.of(reply)));
                    chat.setAttribute("gen_ai.response.finish_reasons", String.valueOf(resp.stopReasonAsString()));
                    if (resp.usage() != null) {
                        chat.setAttribute("gen_ai.usage.input_tokens", resp.usage().inputTokens());
                        chat.setAttribute("gen_ai.usage.output_tokens", resp.usage().outputTokens());
                        in += resp.usage().inputTokens();
                        out += resp.usage().outputTokens();
                    }
                } catch (RuntimeException e) {
                    GenAiTelemetry.fail(chat, e);
                    throw e;
                } finally {
                    chat.end();
                }

                Message reply = resp.output().message();
                messages.add(reply);
                String text = textOf(reply);
                if (!text.isBlank()) { lastText = text; transcript.add(new MemoryStore.Turn("assistant", text)); }

                if (resp.stopReason() == StopReason.GUARDRAIL_INTERVENED) { stop = "guardrail_intervened"; break; }
                if (resp.stopReason() != StopReason.TOOL_USE) { stop = resp.stopReasonAsString(); break; }

                List<ContentBlock> results = new ArrayList<>();
                for (ContentBlock b : reply.content()) {
                    ToolUseBlock use = b.toolUse();
                    if (use == null) continue;
                    used.add(use.name());
                    Map<String, Object> input = Json.mapOf(use.input());
                    Span toolSpan = tel.tool(use.name(), Json.write(input));
                    ToolBox.Outcome o;
                    try (Scope s = GenAiTelemetry.activate(toolSpan)) {
                        o = tools.call(use.name(), input);
                        toolSpan.setAttribute("gen_ai.tool.call.result", o.text());
                        if (o.error()) toolSpan.setAttribute("error.type", "tool_error");
                    } finally {
                        toolSpan.end();
                    }
                    String kept = o.text().length() > MAX_TOOL_TEXT ? o.text().substring(0, MAX_TOOL_TEXT) + " ...(truncated)" : o.text();
                    transcript.add(new MemoryStore.Turn("tool", use.name() + (o.error() ? " failed: " : " returned: ") + kept));
                    results.add(ContentBlock.fromToolResult(ToolResultBlock.builder().toolUseId(use.toolUseId())
                            .content(ToolResultContentBlock.fromText(o.text().isEmpty() ? "(empty)" : o.text()))
                            .status(o.error() ? ToolResultStatus.ERROR : ToolResultStatus.SUCCESS).build()));
                }
                messages.add(Message.builder().role(ConversationRole.USER).content(results).build());
                if (turn == MAX_TURNS) stop = "max_turns";
            }
            agentSpan.setAttribute("gen_ai.task.output", lastText);
            agentSpan.setAttribute("gen_ai.usage.input_tokens", in);
            agentSpan.setAttribute("gen_ai.usage.output_tokens", out);
            return new Result(lastText, stop, used, messages.size(), in, out, transcript);
        } catch (RuntimeException e) {
            GenAiTelemetry.fail(agentSpan, e);
            throw e;
        } finally {
            agentSpan.end();
        }
    }

    static String textOf(Message m) {
        StringBuilder sb = new StringBuilder();
        for (ContentBlock b : m.content()) if (b.text() != null) sb.append(b.text());
        return sb.toString();
    }

    /** Converse needs user/assistant alternation starting with user: merge repeats, drop a leading assistant turn. */
    static List<Message> alternating(List<Message> in) {
        List<Message> out = new ArrayList<>();
        for (Message m : in) {
            if (out.isEmpty() && m.role() == ConversationRole.ASSISTANT) continue;
            if (!out.isEmpty() && out.get(out.size() - 1).role() == m.role()) {
                Message prev = out.remove(out.size() - 1);
                List<ContentBlock> merged = new ArrayList<>(prev.content());
                merged.addAll(m.content());
                out.add(prev.toBuilder().content(merged).build());
            } else {
                out.add(m);
            }
        }
        if (!out.isEmpty() && out.get(out.size() - 1).role() == ConversationRole.USER) {
            out.add(Message.builder().role(ConversationRole.ASSISTANT).content(ContentBlock.fromText("(noted)")).build());
        }
        return out;
    }

    /** OpenTelemetry GenAI message format: [{"role": ..., "parts": [{"type": "text", "content": ...} | tool_call | tool_call_response]}] */
    static String messagesJson(List<Message> msgs) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Message m : msgs) {
            List<Map<String, Object>> parts = new ArrayList<>();
            for (ContentBlock b : m.content()) {
                Map<String, Object> p = new LinkedHashMap<>();
                if (b.text() != null) { p.put("type", "text"); p.put("content", b.text()); }
                else if (b.toolUse() != null) {
                    p.put("type", "tool_call"); p.put("id", b.toolUse().toolUseId()); p.put("name", b.toolUse().name());
                    p.put("arguments", Json.fromDocument(b.toolUse().input()));
                } else if (b.toolResult() != null) {
                    p.put("type", "tool_call_response"); p.put("id", b.toolResult().toolUseId());
                    StringBuilder t = new StringBuilder();
                    b.toolResult().content().forEach(c -> { if (c.text() != null) t.append(c.text()); });
                    p.put("response", t.toString());
                } else continue;
                parts.add(p);
            }
            Map<String, Object> msg = new LinkedHashMap<>();     // ordered: Map.of iteration order changes per JVM
            msg.put("role", m.roleAsString());
            msg.put("parts", parts);
            out.add(msg);
        }
        return Json.write(out);
    }
}
