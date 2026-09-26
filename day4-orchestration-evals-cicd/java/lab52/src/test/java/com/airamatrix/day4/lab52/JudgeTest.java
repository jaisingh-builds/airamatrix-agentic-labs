package com.airamatrix.day4.lab52;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.junit.jupiter.api.Test;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.ModelClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** Offline tests for the judge plumbing - a fake client, no model. Port of test_judge.py (same names). */
class JudgeTest {

    /** Replays canned replies (usage 1000 in / 50 out) and records the prompt it was sent. */
    static class FakeClient implements ModelClient {
        final Deque<String> replies;
        final List<String> prompts = new ArrayList<>();
        FakeClient(String... replies) { this.replies = new ArrayDeque<>(List.of(replies)); }
        @Override public JsonNode messages(List<?> messages, List<?> tools, String system, int maxTokens) {
            prompts.add(String.valueOf(((Map<?, ?>) messages.get(0)).get("content")));
            ObjectNode r = Contracts.object();
            r.putArray("content").addObject().put("type", "text").put("text", replies.poll());
            r.putObject("usage").put("input_tokens", 1000).put("output_tokens", 50);
            return r;
        }
    }

    static Judge judge(ModelClient c) {
        return new Judge(c, "claude-sonnet", new PrintStream(new ByteArrayOutputStream(), true, StandardCharsets.UTF_8));
    }

    static JsonNode json(String s) throws Exception { return Contracts.JSON.readTree(s); }

    @Test
    void test_parses_a_verdict_wrapped_in_prose() {
        Judge.Verdict v = Judge.parseVerdict("Sure. {\"verdict\": \"fail\", \"score\": 2, \"reason\": \"reverts a mitigation\"} Done.");
        assertEquals("fail", v.verdict());
        assertEquals(2, v.score().asInt());
    }

    @Test
    void test_an_unparseable_reply_is_an_error_not_a_pass() {
        for (String bad : List.of("Looks good to me!", "{\"verdict\": \"approve\"}", "")) {
            assertThrows(IllegalArgumentException.class, () -> Judge.parseVerdict(bad), bad);
        }
    }

    @Test
    void test_a_reply_with_only_thinking_is_an_error() {
        ModelClient thinker = (m, t, s, n) -> {
            ObjectNode r = Contracts.object().put("stop_reason", "max_tokens");
            r.putArray("content").addObject().put("type", "thinking").put("thinking", "...");
            r.putObject("usage");
            return r;
        };
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> judge(thinker).judge("q", Contracts.object(), null));
        assertTrue(e.getMessage().contains("max_tokens"), e.getMessage());
        assertTrue(e.getMessage().contains("blocks=['thinking']"), e.getMessage());
    }

    @Test
    void test_reference_is_only_sent_in_reference_mode() throws Exception {
        String p = Judge.judgePrompt("q", json("{\"a\": 1}"), null);
        assertFalse(p.contains("<reference>"));
        assertTrue(Judge.judgePrompt("q", json("{\"a\": 1}"), "facts").contains("<reference>\nfacts\n</reference>"));
        assertTrue(p.contains("<proposal>"));                       // untrusted content is fenced
        assertTrue(p.contains("<proposal>\n{\n  \"a\": 1\n}\n</proposal>"), p);   // json.dumps(indent=2)
    }

    @Test
    void test_agreement_separates_false_passes_from_false_fails() throws Exception {
        List<JsonNode> rows = List.of(json("{\"id\": \"a\", \"human\": \"fail\", \"judge\": \"pass\"}"),
                json("{\"id\": \"b\", \"human\": \"pass\", \"judge\": \"fail\"}"),
                json("{\"id\": \"c\", \"human\": \"pass\", \"judge\": \"pass\"}"),
                json("{\"id\": \"d\", \"human\": \"fail\", \"judge\": null}"));
        ObjectNode a = Judge.agreement(rows);
        assertEquals("[\"a\"]", a.get("false_pass").toString());
        assertEquals("[\"b\"]", a.get("false_fail").toString());
        assertEquals("[\"d\"]", a.get("errors").toString());
        assertEquals(0.333, a.get("agreement").asDouble());
    }

    @Test
    void test_calibrate_uses_the_item_question_and_reference_overrides() throws Exception {
        Map<String, JsonNode> cases = Map.of("k", json("{\"question\": \"case q\", \"reference\": \"case facts\"}"));
        JsonNode items = json("[{\"id\": \"x\", \"case\": \"k\", \"human\": \"pass\", \"proposal\": {}},"
                + " {\"id\": \"y\", \"case\": \"k\", \"human\": \"fail\", \"proposal\": {}, \"question\": \"item q\", \"reference\": \"item facts\"}]");
        FakeClient fc = new FakeClient("{\"verdict\":\"pass\"}", "{\"verdict\":\"pass\"}");
        Judge.Calibration c = judge(fc).calibrate(items, cases, "reference");
        assertTrue(fc.prompts.get(0).contains("case facts"));
        assertTrue(fc.prompts.get(1).contains("item q"));
        assertTrue(fc.prompts.get(1).contains("item facts"));
        assertEquals("[\"y\"]", Judge.agreement(c.rows()).get("false_pass").toString());
        assertEquals(2 * (1000 * 0.000002 + 50 * 0.00001), c.costUsd(), 1e-12);
    }

    @Test
    void test_a_judge_error_is_recorded_not_counted_as_a_verdict() throws Exception {
        Map<String, JsonNode> cases = Map.of("k", json("{\"question\": \"q\", \"reference\": \"f\"}"));
        JsonNode items = json("[{\"id\": \"x\", \"case\": \"k\", \"human\": \"fail\", \"proposal\": {}}]");
        Judge.Calibration c = judge(new FakeClient("LGTM")).calibrate(items, cases, "blind");
        assertTrue(c.rows().get(0).get("judge").isNull());
        assertTrue(c.rows().get(0).get("reason").asText().startsWith("error: judge reply has no JSON"));
        assertEquals("[\"x\"]", Judge.agreement(c.rows()).get("errors").toString());
    }

    @Test
    void test_calibration_set_is_balanced_and_sourced() throws Exception {
        JsonNode items = LabPaths.readJson(LabPaths.lab().resolve("judge_calibration.json")).get("items");
        Set<String> cases = new HashSet<>();
        LabPaths.readJson(LabPaths.lab().resolve("golden/cases.json")).get("cases").forEach(c -> cases.add(c.get("id").asText()));
        for (String h : List.of("pass", "fail")) {
            int n = 0;
            for (JsonNode i : items) if (i.get("human").asText().equals(h)) n++;
            assertTrue(n >= 3, h);
        }
        for (JsonNode i : items) {
            assertTrue(cases.contains(i.get("case").asText()));
            assertFalse(i.path("source").asText().isEmpty());
            assertFalse(i.path("why").asText().isEmpty());
        }
    }
}
