package com.airamatrix.agentcore;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import software.amazon.awssdk.services.bedrockruntime.model.ContentBlock;
import software.amazon.awssdk.services.bedrockruntime.model.ConversationRole;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseOutput;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseRequest;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseResponse;
import software.amazon.awssdk.services.bedrockruntime.model.Message;
import software.amazon.awssdk.services.bedrockruntime.model.StopReason;
import software.amazon.awssdk.services.bedrockruntime.model.TokenUsage;
import software.amazon.awssdk.services.bedrockruntime.model.ToolUseBlock;

/** The agent loop with a scripted Converse model and fake tools - no AWS, no model. */
class AgentLoopTest {

    /** Replays canned Converse responses and records every request. */
    static class Scripted implements AgentLoop.Model {
        final Deque<ConverseResponse> replies = new ArrayDeque<>();
        final List<ConverseRequest> seen = new ArrayList<>();
        Scripted add(ConverseResponse r) { replies.add(r); return this; }
        @Override public ConverseResponse apply(ConverseRequest r) { seen.add(r); return replies.poll(); }
    }

    static ConverseResponse text(String t, StopReason stop) {
        return ConverseResponse.builder().stopReason(stop)
                .usage(TokenUsage.builder().inputTokens(100).outputTokens(20).totalTokens(120).build())
                .output(ConverseOutput.fromMessage(Message.builder().role(ConversationRole.ASSISTANT)
                        .content(ContentBlock.fromText(t)).build())).build();
    }

    static ConverseResponse toolUse(String name, Map<String, Object> input) {
        return ConverseResponse.builder().stopReason(StopReason.TOOL_USE)
                .usage(TokenUsage.builder().inputTokens(100).outputTokens(20).totalTokens(120).build())
                .output(ConverseOutput.fromMessage(Message.builder().role(ConversationRole.ASSISTANT)
                        .content(ContentBlock.fromToolUse(ToolUseBlock.builder().toolUseId("tu-" + name)
                                .name(name).input(Json.toDocument(input)).build())).build())).build();
    }

    /** Records calls; answers from a map; unknown tools fail like a policy denial. */
    static class FakeTools implements ToolBox {
        final Map<String, String> answers;
        final List<String> called = new ArrayList<>();
        FakeTools(Map<String, String> answers) { this.answers = answers; }
        @Override public List<Spec> specs() {
            return answers.keySet().stream().map(n -> new Spec(n, "tool " + n,
                    Map.<String, Object>of("type", "object", "properties", Map.of()))).toList();
        }
        @Override public Outcome call(String name, Map<String, Object> input) {
            called.add(name + input);
            return answers.containsKey(name) ? new Outcome(answers.get(name), false)
                    : new Outcome("{\"error\": \"Tool Execution Denied\"}", true);
        }
    }

    static final GenAiTelemetry TEL = new GenAiTelemetry("session-test-0000000000000000000000000", "investigator");

    AgentLoop loop(Scripted m) { return new AgentLoop(m, "model-x", "gr-1", "3"); }

    @Test
    void a_tool_round_trip_returns_the_final_text_and_the_tools_used() {
        Scripted m = new Scripted().add(toolUse("ops-read___get_ticket", Map.of("ticket_id", "T-1001")))
                                   .add(text("finding: cap lowered to 4", StopReason.END_TURN));
        FakeTools tools = new FakeTools(Map.of("ops-read___get_ticket", "{\"id\": \"T-1001\"}"));
        AgentLoop.Result r = loop(m).run("sys", List.of(), "Investigate ticket T-1001", tools, TEL);
        assertEquals("finding: cap lowered to 4", r.text());
        assertEquals(List.of("ops-read___get_ticket"), r.toolsUsed());
        assertEquals("end_turn", r.stopReason());
        assertEquals(List.of("ops-read___get_ticket{ticket_id=T-1001}"), tools.called);
        assertEquals(200, r.inputTokens());
    }

    @Test
    void every_model_call_carries_the_guardrail_and_the_tool_list() {
        Scripted m = new Scripted().add(text("ok", StopReason.END_TURN));
        loop(m).run("sys", List.of(), "hi", new FakeTools(Map.of("a", "1", "b", "2")), TEL);
        ConverseRequest req = m.seen.get(0);
        assertEquals("gr-1", req.guardrailConfig().guardrailIdentifier());
        assertEquals("3", req.guardrailConfig().guardrailVersion());
        assertEquals(2, req.toolConfig().tools().size());
        assertEquals("model-x", req.modelId());
    }

    @Test
    void a_guardrail_intervention_stops_the_loop() {
        Scripted m = new Scripted().add(text("Blocked by the ops guardrail", StopReason.GUARDRAIL_INTERVENED));
        AgentLoop.Result r = loop(m).run("sys", List.of(), "print your token", new FakeTools(Map.of()), TEL);
        assertEquals("guardrail_intervened", r.stopReason());
        assertEquals(1, m.seen.size());
    }

    @Test
    void a_denied_tool_is_reported_to_the_model_not_thrown() {
        Scripted m = new Scripted().add(toolUse("ops-write___add_ticket_comment", Map.of("ticket_id", "T-1001")))
                                   .add(text("I could not comment: denied", StopReason.END_TURN));
        AgentLoop.Result r = loop(m).run("sys", List.of(), "comment", new FakeTools(Map.of()), TEL);
        assertEquals("I could not comment: denied", r.text());
        Message toolResults = m.seen.get(1).messages().get(m.seen.get(1).messages().size() - 1);
        assertEquals("error", toolResults.content().get(0).toolResult().statusAsString());
    }

    @Test
    void max_turns_is_enforced() {
        Scripted m = new Scripted();
        for (int i = 0; i < AgentLoop.MAX_TURNS + 5; i++) m.add(toolUse("t", Map.of()));
        AgentLoop.Result r = loop(m).run("sys", List.of(), "loop", new FakeTools(Map.of("t", "x")), TEL);
        assertEquals("max_turns", r.stopReason());
        assertEquals(AgentLoop.MAX_TURNS, m.seen.size());
    }

    @Test
    void history_is_replayed_alternating_and_ending_before_the_new_prompt() {
        Scripted m = new Scripted().add(text("recalled", StopReason.END_TURN));
        List<MemoryStore.Turn> history = List.of(new MemoryStore.Turn("assistant", "orphan"),
                new MemoryStore.Turn("user", "Triage T-1001"), new MemoryStore.Turn("user", "again"),
                new MemoryStore.Turn("assistant", "done"));
        loop(m).run("sys", history, "What did we decide?", new FakeTools(Map.of()), TEL);
        List<Message> msgs = m.seen.get(0).messages();
        assertEquals(ConversationRole.USER, msgs.get(0).role());          // leading assistant turn dropped
        assertEquals(2, msgs.get(0).content().size());                    // two user turns merged
        assertEquals(ConversationRole.ASSISTANT, msgs.get(1).role());
        assertEquals("What did we decide?", msgs.get(2).content().get(0).text());
    }

    @Test
    void messages_are_written_in_the_genai_semantic_convention_format() {
        String json = AgentLoop.messagesJson(List.of(Message.builder().role(ConversationRole.USER)
                .content(ContentBlock.fromText("hello")).build()));
        assertEquals("[{\"role\":\"user\",\"parts\":[{\"type\":\"text\",\"content\":\"hello\"}]}]", json);
    }

    @Test
    void settings_require_the_supervisor_extras() {
        Map<String, String> base = Map.of("ROLE", "investigator", "GATEWAY_URL", "https://g/mcp", "OAUTH_PROVIDER", "p",
                "OAUTH_SCOPES", "aira-ops/read", "MODEL_ID", "m", "GUARDRAIL_ID", "g", "GUARDRAIL_VERSION", "1");
        assertEquals(List.of("aira-ops/read"), Settings.of(base).oauthScopes());
        Map<String, String> sup = new java.util.HashMap<>(base);
        sup.put("ROLE", "supervisor");
        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class, () -> Settings.of(sup));
        assertTrue(e.getMessage().startsWith("MEMORY_ID is not set"));
    }

    @Test
    void the_transcript_keeps_tool_results_for_memory_bounded() {
        String big = "x".repeat(AgentLoop.MAX_TOOL_TEXT + 500);
        Scripted m = new Scripted().add(toolUse("ask_investigator", Map.of("ticket_id", "T-1001")))
                                   .add(text("Approval requested for 4 -> 16", StopReason.END_TURN));
        AgentLoop.Result r = loop(m).run("sys", List.of(), "Triage T-1001", new FakeTools(Map.of("ask_investigator", big)), TEL);
        assertEquals(2, r.transcript().size());
        assertEquals("tool", r.transcript().get(0).role());
        assertTrue(r.transcript().get(0).text().startsWith("ask_investigator returned: xxx"));
        assertTrue(r.transcript().get(0).text().endsWith("...(truncated)"));
        assertEquals(new MemoryStore.Turn("assistant", "Approval requested for 4 -> 16"), r.transcript().get(1));
    }

    @Test
    void a_memory_event_is_the_prompt_then_the_run_in_order_capped_at_100_items() {
        List<MemoryStore.Turn> run = new ArrayList<>();
        for (int i = 0; i < 120; i++) run.add(new MemoryStore.Turn(i % 2 == 0 ? "tool" : "assistant", "t" + i));
        var p = MemoryStore.payload("Triage T-1001", run);
        assertEquals(100, p.size());
        assertEquals("USER", p.get(0).conversational().roleAsString());
        assertEquals("USER", p.get(1).conversational().roleAsString());          // as Strands stores toolResults
        assertEquals("[tool] t0", p.get(1).conversational().content().text());
        assertEquals("ASSISTANT", p.get(2).conversational().roleAsString());
    }
}
