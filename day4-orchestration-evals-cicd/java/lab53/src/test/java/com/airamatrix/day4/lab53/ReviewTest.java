package com.airamatrix.day4.lab53;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.ModelClient;
import com.airamatrix.labkit.GatewayError;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.TextNode;

/**
 * Lab 5.3 tests - every control around the model, no model. Port of lab5-3-pr-review/test_review.py.
 * A scripted ModelClient plays the reviewer: it records what it was sent and returns a canned reply.
 */
class ReviewTest {

    /** A planted fake credential, built from parts so the repo's secret scanners do not flag it. */
    static final String FAKE_LIVE = "live-" + "7f3a9c2e" + "5b1d4a6f8e0c";

    static final String DIFF = """
            diff --git a/svc/apply.py b/svc/apply.py
            index 1111111..2222222 100644
            --- a/svc/apply.py
            +++ b/svc/apply.py
            @@ -10,6 +10,8 @@ def apply(store, rid, token):
                 a = store.approval(rid)
            -    if not a or a["decision"] != "approve":
            -        raise GateError("no approval on record")
            +    if store.run(rid)["status"] == "approved":
            +        pass
                 op_id = store.operation(rid) or new_op_id()
            +    API_TOKEN = "@LIVE@"
                 return send(op_id, token)
            diff --git a/README.md b/README.md
            --- a/README.md
            +++ b/README.md
            @@ -1,2 +1,3 @@
             # Labs
            +Run the pipeline with `python3 pipeline.py run`.
            """.replace("@LIVE@", FAKE_LIVE);

    static final String DIFF_NO_SECRET = String.join("", DIFF.lines().filter(l -> !l.contains("API_TOKEN"))
            .map(l -> l + "\n").toList());

    static final String GOOD_FINDING = """
            {"severity": "blocker", "file": "svc/apply.py", "line": 11, "title": "Approval check replaced by status check",
             "evidence": "if store.run(rid)[\\"status\\"] == \\"approved\\":", "why": "status field is not the decision record"}""";

    /** A model that replays canned responses and records every request it was sent. */
    static final class Scripted implements ModelClient {
        final Deque<Object> replies = new ArrayDeque<>();
        final List<String> requests = new ArrayList<>();          // messages+tools+system, serialised at call time
        final List<List<?>> tools = new ArrayList<>();
        final List<Integer> maxTokens = new ArrayList<>();
        long sleepMs;

        Scripted add(Object r) { replies.add(r); return this; }

        @Override
        public JsonNode messages(List<?> messages, List<?> toolList, String system, int max) {
            try {
                requests.add(Contracts.JSON.writeValueAsString(Map.of("messages", messages, "tools", toolList, "system", system)));
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
            tools.add(toolList);
            maxTokens.add(max);
            if (sleepMs > 0) {
                try { Thread.sleep(sleepMs); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            }
            Object r = replies.isEmpty() ? text("still thinking") : replies.poll();
            if (r instanceof RuntimeException ex) throw ex;
            return (JsonNode) r;
        }

        /** The user prompt of the first call - what the model was sent. */
        String sent() {
            try {
                return Contracts.JSON.readTree(requests.get(0)).path("messages").get(0).path("content").asText();
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        }
    }

    static ObjectNode response(String stop) {
        ObjectNode r = Contracts.object();
        r.put("type", "message").put("stop_reason", stop);
        r.putObject("usage").put("input_tokens", 20_000).put("output_tokens", 800);
        r.putArray("content");
        return r;
    }

    static JsonNode text(String t) {
        ObjectNode r = response("end_turn");
        ((ArrayNode) r.get("content")).addObject().put("type", "text").put("text", t);
        return r;
    }

    static JsonNode submit(String inputJson) {
        ObjectNode r = response("tool_use");
        try {
            ((ArrayNode) r.get("content")).addObject().put("type", "tool_use").put("id", "tu_" + System.nanoTime())
                    .put("name", Review.SUBMIT).set("input", Contracts.JSON.readTree(inputJson));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return r;
    }

    static JsonNode reply(String... findings) {
        return submit("{\"summary\": \"Removes the approval check.\", \"findings\": [" + String.join(",", findings) + "]}");
    }

    static String finding(Map<String, Object> overrides) {
        try {
            ObjectNode f = (ObjectNode) Contracts.JSON.readTree(GOOD_FINDING);
            overrides.forEach((k, v) -> f.set(k, Contracts.JSON.valueToTree(v)));
            return Contracts.JSON.writeValueAsString(f);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @TempDir Path tmp;
    Map<String, String> env;
    Scripted model;
    boolean keyMissing;
    Review review;
    ByteArrayOutputStream stdout, stderr;

    @BeforeEach
    void setUp() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), DIFF);
        env = new HashMap<>();
        env.put("GITHUB_TOKEN", "ghs_" + "x".repeat(36));
        env.put("AIRA_OPS_TOKEN", "ops-admin-123456789");
        model = new Scripted();
        stdout = new ByteArrayOutputStream();
        stderr = new ByteArrayOutputStream();
        review = new Review(env::get, () -> {
            if (keyMissing) throw new IllegalStateException("Missing: ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN");
            return new Review.Reviewer(model, "claude-sonnet");
        }, new PrintStream(stdout, true, StandardCharsets.UTF_8), new PrintStream(stderr, true, StandardCharsets.UTF_8));
    }

    int run(String... extra) {
        List<String> args = new ArrayList<>(List.of("--diff", tmp.resolve("change.patch").toString(),
                "--repo", tmp.toString(), "--out", tmp.resolve("out").toString()));
        args.addAll(List.of(extra));
        return review.run(args.toArray(String[]::new));
    }

    JsonNode reviewJson() throws Exception {
        return Contracts.JSON.readTree(Files.readString(tmp.resolve("out").resolve("review.json")));
    }

    String reviewMd() throws Exception {
        return Files.readString(tmp.resolve("out").resolve("review.md"));
    }

    // --- reading the diff
    @Test
    void changedLinesAreNumberedOnTheNewSide() {
        Map<String, Map<Integer, String>> ch = Review.changedLines(DIFF);
        assertEquals("    if store.run(rid)[\"status\"] == \"approved\":", ch.get("svc/apply.py").get(11));
        assertTrue(ch.get("svc/apply.py").containsKey(14));
        assertEquals(List.of(2), new ArrayList<>(ch.get("README.md").keySet()));
    }

    @Test
    void removedLinesAreAnchoredWhereTheyWere() {
        Map<String, Map<Integer, String>> rm = Review.removedLines(DIFF);
        assertEquals("if not a or a[\"decision\"] != \"approve\":         raise GateError(\"no approval on record\")",
                rm.get("svc/apply.py").get(11));
    }

    // --- secrets: found without the model, never sent to it
    @Test
    void aCommittedSecretIsABlockerAndIsNotSentToTheModel() throws Exception {
        model.add(reply());
        assertEquals(2, run());
        JsonNode rj = reviewJson();
        assertEquals(1, rj.path("findings").size());
        assertEquals("pattern", rj.path("findings").get(0).path("source").asText());
        assertEquals(14, rj.path("findings").get(0).path("line").asInt());
        String sent = model.sent();
        assertFalse(sent.contains(FAKE_LIVE));
        assertFalse(reviewMd().contains(FAKE_LIVE));
        assertTrue(sent.contains("API_TOKEN = \"[REDACTED]\""), "the name stays, so the model sees what it was");
    }

    /** Java analogue of test_the_model_gets_the_gateway_key_and_nothing_else_secret. */
    @Test
    void theRequestCarriesNothingFromTheEnvironment() {
        model.add(reply());
        run();
        String request = model.requests.get(0);
        for (String leaked : env.values()) assertFalse(request.contains(leaked), "env value reached the model");
        assertFalse(request.contains(tmp.toString()), "the raw checkout path reached the model");
    }

    // --- the reviewer call (was: the headless invocation)
    @Test
    void theReviewerCallHasOneSubmitToolAndIsBounded() {
        model.add(reply());
        run();
        assertEquals(1, model.requests.size(), "one call when the answer is valid");
        List<?> tools = model.tools.get(0);
        assertEquals(1, tools.size(), "no aira-ops tools: it sees only what we send");
        JsonNode tool = Contracts.JSON.valueToTree(tools.get(0));
        assertEquals(Review.SUBMIT, tool.path("name").asText());
        assertEquals(Review.FINDINGS, tool.path("input_schema"));
        assertTrue(model.maxTokens.get(0) <= 4000);
        String system = systemOf(0);
        assertTrue(system.startsWith(Review.SYSTEM), "SYSTEM is review.py's, verbatim");
    }

    String systemOf(int call) {
        try {
            return Contracts.JSON.readTree(model.requests.get(call)).path("system").asText();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    // --- verification: the model's findings are claims
    @Test
    void aVerifiedBlockerBlocks() throws Exception {
        model.add(reply(GOOD_FINDING));
        assertEquals(2, run());
        List<String> sources = new ArrayList<>();
        reviewJson().path("findings").forEach(f -> sources.add(f.path("source").asText()));
        assertTrue(sources.contains("model"));
    }

    @Test
    void findingsThatDoNotPointAtAChangedLineAreDropped() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), DIFF_NO_SECRET);
        model.add(reply(finding(Map.of("file", "svc/other.py")), finding(Map.of("line", 40)),
                finding(Map.of("evidence", "os.system(user_input)"))));
        assertEquals(0, run());
        JsonNode rj = reviewJson();
        assertEquals(0, rj.path("findings").size());
        assertEquals(3, rj.path("dropped").size());
        assertEquals("file not changed in this PR", rj.path("dropped").get(0).path("dropped").asText());
        assertEquals("line 40 is not a changed line", rj.path("dropped").get(1).path("dropped").asText());
        assertEquals("evidence does not match the changed lines", rj.path("dropped").get(2).path("dropped").asText());
    }

    @Test
    void aPrThatOnlyDeletesACheckCanStillBeBlocked() throws Exception {
        // No added lines at all - the most dangerous kind of PR must not be unreviewable.
        Files.writeString(tmp.resolve("change.patch"), """
                diff --git a/svc/apply.py b/svc/apply.py
                --- a/svc/apply.py
                +++ b/svc/apply.py
                @@ -10,5 +10,3 @@ def apply(store, rid, token):
                     a = store.approval(rid)
                -    if not a or a["decision"] != "approve":
                -        raise GateError("no approval on record")
                     op_id = store.operation(rid) or new_op_id()
                """);
        model.add(reply(finding(Map.of("line", 11, "title", "Approval check deleted",
                "evidence", "-    if not a or a[\"decision\"] != \"approve\":"))));
        assertEquals(2, run(), stderr.toString());
        assertEquals("Approval check deleted", reviewJson().path("findings").get(0).path("title").asText());
    }

    @Test
    void aFindingWithNoEvidenceIsDropped() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), DIFF_NO_SECRET);
        model.add(reply(finding(Map.of("evidence", "   "))));
        assertEquals(0, run());
        assertEquals("no evidence quoted", reviewJson().path("dropped").get(0).path("dropped").asText());
    }

    @Test
    void anUnquotedTokenAssignmentIsABlockerAndIsMasked() throws Exception {
        // the course's own token format, exactly as a shell line would carry it
        String hexed = "5f0e3c7a" + "9b2d4e6f" + "8a1c3e5d" + "7b9f0a2c";   // parts: not a literal for scanners
        Files.writeString(tmp.resolve("change.patch"), """
                diff --git a/run.sh b/run.sh
                --- a/run.sh
                +++ b/run.sh
                @@ -1,1 +1,2 @@
                 #!/bin/sh
                +export AIRA_OPS_TOKEN=%s
                """.formatted(hexed));
        model.add(reply());
        assertEquals(2, run());
        assertEquals("pattern", reviewJson().path("findings").get(0).path("source").asText());
        assertFalse(model.sent().contains(hexed));
        String placeholder = "sk-" + "PASTE-YOUR-KEY-HERE";
        for (String ok : List.of("AIRA_OPS_TOKEN=${AIRA_OPS_TOKEN}", "token = secrets.token_hex(16)", "max_tokens=4000",
                "ANTHROPIC_AUTH_TOKEN=" + placeholder)) {
            assertEquals(List.of(), Review.secretFindings(Map.of("x", Map.of(1, ok))), ok);
        }
    }

    @Test
    void minorFindingsDoNotBlock() {
        try { Files.writeString(tmp.resolve("change.patch"), DIFF_NO_SECRET); } catch (Exception e) { throw new IllegalStateException(e); }
        model.add(reply(finding(Map.of("severity", "minor"))));
        assertEquals(0, run());
    }

    // --- failing closed
    /** Java analogue of test_a_model_error_is_exit_1_even_if_the_process_exits_0. */
    @Test
    void aGatewayErrorIsExit1() throws Exception {
        model.add(new GatewayError(529, "{\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}}"));
        assertEquals(1, run());
        assertTrue(reviewJson().path("error").asText().contains("is_error=true"));
        assertTrue(reviewMd().startsWith("### Agent review: could not run"));
        assertTrue(stderr.toString().contains("review failed: RuntimeError: reviewer is_error=true gateway status 529"));
    }

    @Test
    void anErrorResponseIsExit1() throws Exception {
        ObjectNode err = Contracts.object().put("type", "error");
        err.putObject("error").put("type", "overloaded_error").put("message", "API Error: 529 overloaded");
        model.add(err);
        assertEquals(1, run());
        assertTrue(reviewJson().path("error").asText().contains("is_error=true subtype=overloaded_error"));
    }

    @Test
    void aTextOnlyAnswerIsRetriedOnceThenExit1() throws Exception {
        model.add(text("Looks good to me!")).add(text("Really, looks good."));
        assertEquals(1, run());
        assertEquals(2, model.requests.size(), "exactly one retry");
        assertTrue(reviewJson().path("error").asText().contains("no submit_findings call"));
    }

    @Test
    void aContractErrorIsRetriedOnce() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), DIFF_NO_SECRET);
        model.add(submit("{\"summary\": \"x\", \"findings\": [{\"severity\": \"critical\"}]}")).add(reply(GOOD_FINDING));
        assertEquals(2, run(), stderr.toString());
        assertEquals(2, model.requests.size());
        JsonNode second = Contracts.JSON.readTree(model.requests.get(1)).path("messages");
        JsonNode feedback = second.get(second.size() - 1).path("content").get(0);
        assertEquals("tool_result", feedback.path("type").asText());
        assertTrue(feedback.path("is_error").asBoolean());
        assertTrue(feedback.path("content").asText().startsWith("contract error: "));
        assertEquals(2, reviewJson().path("turns").asInt());
    }

    @Test
    void aContractErrorTwiceIsExit1() throws Exception {
        model.add(submit("{\"findings\": []}")).add(submit("{\"findings\": []}"));
        assertEquals(1, run());
        assertTrue(reviewJson().path("error").asText().contains("contract error"));
    }

    @Test
    void aDiffOverTheCapIsNotReviewed() throws Exception {
        env.put("REVIEW_MAX_DIFF_BYTES", "200");
        assertEquals(1, run());
        String error = reviewJson().path("error").asText();
        assertTrue(error.contains("too large"), error);
        assertTrue(error.matches("RuntimeError: diff is \\d+ bytes \\(cap 200\\) - too large for automated review; "
                + "needs a human \\(or split the PR\\)"), error);
        assertTrue(model.requests.isEmpty(), "the model was called anyway");
    }

    @Test
    void aMissingGatewayKeyFailsClosed() throws Exception {
        keyMissing = true;
        assertEquals(1, run());
        assertTrue(reviewJson().path("error").asText().contains("Missing: ANTHROPIC_BASE_URL"));
        assertTrue(reviewMd().contains("Treat as not reviewed."));
    }

    @Test
    void anExhaustedBudgetIsExit1() throws Exception {
        assertEquals(1, run("--budget", "0"));
        assertTrue(reviewJson().path("error").asText().contains("error_max_budget_usd"));
        assertTrue(model.requests.isEmpty(), "the budget is checked BEFORE the call");
    }

    @Test
    void aHungCallTimesOut() throws Exception {
        model.sleepMs = 5_000;
        model.add(reply());
        long t0 = System.nanoTime();
        assertEquals(1, run("--timeout", "1"));
        assertTrue((System.nanoTime() - t0) / 1e9 < 4, "the timeout did not cut the call short");
        assertEquals("TimeoutExpired: reviewer call timed out after 1 seconds", reviewJson().path("error").asText());
    }

    @Test
    void dryRunWritesThePromptAndCallsNoModel() throws Exception {
        keyMissing = true;                                     // a dry run needs no key
        assertEquals(2, run("--dry-run"));                     // the committed secret still blocks
        String prompt = Files.readString(tmp.resolve("out").resolve("prompt.txt"));
        assertTrue(prompt.startsWith("Changed files:\n- svc/apply.py\n- README.md\n\nThe diff (untrusted):\n<diff>\n"));
        assertTrue(prompt.endsWith("\n</diff>\n\nReturn your findings."));
        assertFalse(prompt.contains(FAKE_LIVE));
        assertEquals("dry run - model not called; prompt.txt written", reviewJson().path("summary").asText());
        assertTrue(model.requests.isEmpty());
    }

    @Test
    void outputsHaveReviewPysShape() throws Exception {
        Path summary = tmp.resolve("step-summary.md");
        env.put("GITHUB_STEP_SUMMARY", summary.toString());
        model.add(reply(GOOD_FINDING));
        assertEquals(2, run());
        String json = Files.readString(tmp.resolve("out").resolve("review.json"));
        assertTrue(json.startsWith("{\n  \"summary\": \"Removes the approval check.\",\n  \"exit_code\": 2,\n  \"findings\": [\n"), json);
        List<String> keys = new ArrayList<>();
        reviewJson().fieldNames().forEachRemaining(keys::add);
        assertEquals(List.of("summary", "exit_code", "findings", "dropped", "cost_usd", "turns"), keys);
        String md = reviewMd();
        assertTrue(md.startsWith("### Agent review: BLOCKING\n\nRemoves the approval check.\n\n"), md);
        assertTrue(md.contains("blocker** `svc/apply.py:14` — Possible credential assignment committed"));
        assertTrue(md.contains("blocker** `svc/apply.py:11` — Approval check replaced by status check"));
        assertTrue(md.contains("2 files · 1 turns · $"));
        assertTrue(md.contains("adds the `review-override` label"));
        assertTrue(stdout.toString(StandardCharsets.UTF_8).contains("### Agent review: BLOCKING"));
        assertEquals(md + "\n", Files.readString(summary));
    }

    @Test
    void noChangedLinesIsNotAnError() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), "");
        assertEquals(0, run());
        assertEquals("No changed lines to review.", reviewJson().path("summary").asText());
        assertTrue(model.requests.isEmpty());
    }

    @Test
    void aBadFlagIsAUsageErrorLikeArgparse() {
        assertEquals(2, review.run(new String[]{"--nope"}));
        assertTrue(stderr.toString().contains("usage: lab53"));
        assertEquals(0, review.run(new String[]{"--help"}));
    }

    @Test
    void theModelSeesOnlyASanitisedCopyAndHasNoTools() throws Exception {
        Path repo = tmp.resolve("repo");
        Files.createDirectories(repo);
        Git.run(repo, "init", "-q", "-b", "main");
        Files.writeString(repo.resolve("app.py"), "x = 1\n");
        Git.run(repo, "add", "-A");
        Git.run(repo, "commit", "-qm", "base");
        Git.run(repo, "switch", "-qc", "feat");
        Files.writeString(repo.resolve("workshop_env.py"), "AIRA_OPS_APPLY_TOKEN = \"" + DemoPrs.FAKE_APPLY_TOKEN + "\"\n");
        String fakeKey = "sk-" + "live-should-never-be-read-0000";     // built at runtime: the repo's commit hook scans for literals
        Files.writeString(repo.resolve(".env"), "ANTHROPIC_AUTH_TOKEN=" + fakeKey + "\n");
        Git.run(repo, "add", "-f", "-A");
        Git.run(repo, "commit", "-qm", "feat");
        model.add(reply());
        int code = review.run(new String[]{"--repo", repo.toString(), "--base", "main", "--head", "feat",
                "--out", tmp.resolve("out").toString()});
        assertEquals(2, code, stderr.toString());                          // the committed token is a blocker
        assertEquals(1, model.requests.size());
        assertEquals(1, model.tools.get(0).size(), "only the submit tool");
        String sent = model.sent();
        assertTrue(sent.contains("<file path=\"workshop_env.py\">"), sent);  // context comes from the sanitised copy
        assertTrue(sent.contains("AIRA_OPS_APPLY_TOKEN = \"[REDACTED]\""));
        assertFalse(sent.contains("<file path=\".env\">"), "a secret-named file was sent as context");
        for (String secret : List.of(DemoPrs.FAKE_APPLY_TOKEN, fakeKey)) assertFalse(model.requests.get(0).contains(secret));
        assertFalse(sent.contains(repo.toString()), "the raw checkout path reached the model");
        assertNotNull(review.lastWorkspace);
        assertFalse(Files.exists(review.lastWorkspace), "the temporary directory was not cleaned up");
    }

    @Test
    void theWorkspaceDropsSecretFilesAndMasksTheRest() throws Exception {
        Path repo = tmp.resolve("repo2");
        Files.createDirectories(repo.resolve("cfg"));
        Files.writeString(repo.resolve("cfg/server.pem"), "-----BEGIN RSA " + "PRIVATE KEY-----\n");   // parts: a fixture, not a key
        Files.writeString(repo.resolve("settings.py"), "DB_PASSWORD = \"hunter2hunter2hunter2\"\n");
        Files.writeString(repo.resolve("app.py"), "print('hi')\n");
        Git.run(repo, "init", "-q", "-b", "main");
        Git.run(repo, "add", "-f", "-A");
        Git.run(repo, "commit", "-qm", "base");
        Workspace ws = Workspace.sanitized(repo, "HEAD");
        try {
            assertFalse(Files.exists(ws.dir().resolve("cfg/server.pem")));
            assertFalse(Files.exists(ws.dir().resolve(".git")));
            assertEquals("DB_PASSWORD = \"[REDACTED]\"\n", Files.readString(ws.dir().resolve("settings.py")));
            assertEquals("print('hi')\n", Files.readString(ws.dir().resolve("app.py")));
            assertEquals(1, ws.dropped());
            assertEquals(1, ws.masked());
        } finally {
            Workspace.deleteRecursively(ws.dir());
        }
    }

    @Test
    void aFailedArchiveInsideARepoIsAnErrorNotACopyOfTheWorkingTree() throws Exception {
        Path repo = tmp.resolve("repo3");
        Files.createDirectories(repo);
        Files.writeString(repo.resolve("app.py"), "x = 1\n");
        Git.run(repo, "init", "-q", "-b", "main");
        Git.run(repo, "add", "-A");
        Git.run(repo, "commit", "-qm", "base");
        IOException e = assertThrows(IOException.class, () -> Workspace.sanitized(repo, "no-such-rev"));
        assertTrue(e.getMessage().startsWith("git archive no-such-rev failed"), e.getMessage());
        Path plain = tmp.resolve("plain");                          // not a repo: a plain copy is right
        Files.createDirectories(plain);
        Files.writeString(plain.resolve("app.py"), "x = 1\n");
        Workspace ws = Workspace.sanitized(plain, "HEAD");
        try {
            assertEquals("x = 1\n", Files.readString(ws.dir().resolve("app.py")));
        } finally {
            Workspace.deleteRecursively(ws.dir());
        }
    }

    @Test
    void jsonIsEscapedLikePythonsEnsureAscii() {
        // json.dumps("\x7f\u00e9\n") == '"\\u007f\\u00e9\\n"'
        assertEquals("\"\\u007f\\u00e9\\n\"", PyJson.dumps(TextNode.valueOf("\u007f\u00e9\n")));
    }

    // --- the lab itself
    @Test
    void findingsSchemaIsReviewPys() {
        assertEquals(List.of("summary", "findings"),
                List.of(Review.FINDINGS.path("required").get(0).asText(), Review.FINDINGS.path("required").get(1).asText()));
        JsonNode item = Review.FINDINGS.path("properties").path("findings").path("items");
        assertEquals("[\"blocker\",\"major\",\"minor\",\"nit\"]", item.path("properties").path("severity").path("enum").toString());
        assertEquals(15, Review.FINDINGS.path("properties").path("findings").path("maxItems").asInt());
        assertFalse(item.path("additionalProperties").asBoolean(true));
    }

    @Test
    void theExerciseRegionsAreMarked() throws Exception {
        String src = Files.readString(Paths.get("src/main/java/com/airamatrix/day4/lab53/Review.java"));
        for (int n = 1; n <= 3; n++) {
            Matcher m = Pattern.compile("// >>> TODO " + n + ": .*?// <<< TODO " + n + "\\n", Pattern.DOTALL).matcher(src);
            assertTrue(m.find(), "TODO " + n + " block missing");
            assertFalse(m.find(), "TODO " + n + " block appears twice");
        }
    }

    // --- live: costs money, never runs by default
    @Test
    @EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
    void liveReviewOfTheDeletedApprovalCheckBlocks() throws Exception {
        Files.writeString(tmp.resolve("change.patch"), DIFF_NO_SECRET);
        Review live = new Review(System::getenv, Review::fromConfig,
                new PrintStream(stdout, true, StandardCharsets.UTF_8), new PrintStream(stderr, true, StandardCharsets.UTF_8));
        int code = live.run(new String[]{"--diff", tmp.resolve("change.patch").toString(), "--repo", tmp.toString(),
                "--out", tmp.resolve("out").toString(), "--budget", "0.20"});
        assertEquals(2, code, stderr + reviewMd());
        assertTrue(reviewJson().path("cost_usd").asDouble() < 0.20);
    }

    /** git for temp repos: a fixed identity, no signing, no hooks from the user's global config. */
    static final class Git {
        static void run(Path repo, String... args) throws Exception {
            List<String> cmd = new ArrayList<>(List.of("git", "-c", "commit.gpgsign=false", "-c",
                    "core.hooksPath=" + repo.resolve(".no-hooks")));
            cmd.addAll(List.of(args));
            Proc.Result r = Proc.run(cmd, repo, Map.of("GIT_AUTHOR_NAME", "t", "GIT_AUTHOR_EMAIL", "t@t",
                    "GIT_COMMITTER_NAME", "t", "GIT_COMMITTER_EMAIL", "t@t"), 60);
            if (r.code() != 0) throw new IllegalStateException(String.join(" ", cmd) + ": " + r.err());
        }
    }
}
