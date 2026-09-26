package com.airamatrix.day4.lab53;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;

/** demo-prs against copies of the real Day 4 files: the edits still apply, and the reviewer reads the result. */
class DemoPrsTest {

    static final List<String> TOUCHED = List.of(
            "common/trace_view.py", "lab5-1-handoff/pipeline.py", "lab5-1-handoff/agents.py");

    /** No signing, no hooks from the user's global git config, a fixed identity. */
    static final Map<String, String> GIT_ENV = Map.of(
            "GIT_CONFIG_COUNT", "2",
            "GIT_CONFIG_KEY_0", "commit.gpgsign", "GIT_CONFIG_VALUE_0", "false",
            "GIT_CONFIG_KEY_1", "core.hooksPath", "GIT_CONFIG_VALUE_1", "/nonexistent-lab53-hooks",
            "GIT_AUTHOR_NAME", "t", "GIT_AUTHOR_EMAIL", "t@t", "GIT_COMMITTER_NAME", "t", "GIT_COMMITTER_EMAIL", "t@t");

    @TempDir Path tmp;

    void git(Path wt, String... args) throws Exception {
        new DemoPrs(GIT_ENV, System.out).git(wt, args);
    }

    @Test
    void theFiveDemoBranchesAreBuiltAndReviewable() throws Exception {
        Path wt = tmp.resolve("d4wt");
        Path d4 = Spans.repoRoot().resolve(DemoPrs.D4);
        for (String f : TOUCHED) {
            Path dst = wt.resolve(DemoPrs.D4).resolve(f);
            Files.createDirectories(dst.getParent());
            Files.copy(d4.resolve(f), dst);
        }
        Files.createDirectories(wt);
        git(wt, "init", "-q", "-b", "day4");
        git(wt, "add", "-A");
        git(wt, "commit", "-qm", "base");

        ByteArrayOutputStream out = new ByteArrayOutputStream(), err = new ByteArrayOutputStream();
        int code = new DemoPrs(GIT_ENV, new PrintStream(out, true, StandardCharsets.UTF_8))
                .run(new String[]{"--base", "day4", "--worktree", wt.toString()}, new PrintStream(err, true, StandardCharsets.UTF_8));
        assertEquals(0, code, err.toString());
        String printed = out.toString(StandardCharsets.UTF_8);
        for (String branch : DemoPrs.DEMOS.keySet()) assertTrue(printed.contains(branch), printed);
        assertTrue(printed.contains(String.format("%-28s %s", "demo/apply-retry", "apply: retry transient failures before giving up")));

        // the reviewer, dry run (no model): the committed token is a blocker by pattern and is never in the prompt
        Review review = new Review(k -> null, () -> { throw new AssertionError("dry run called the model"); },
                new PrintStream(new ByteArrayOutputStream()), new PrintStream(new ByteArrayOutputStream()));
        Path rv = tmp.resolve("rv");
        assertEquals(2, review.run(new String[]{"--repo", wt.toString(), "--base", "day4", "--head", "demo/workshop-env",
                "--dry-run", "--out", rv.resolve("w").toString()}));
        JsonNode rj = Contracts.JSON.readTree(Files.readString(rv.resolve("w").resolve("review.json")));
        assertEquals("pattern", rj.path("findings").get(0).path("source").asText());
        assertFalse(Files.readString(rv.resolve("w").resolve("prompt.txt")).contains(DemoPrs.FAKE_APPLY_TOKEN));

        assertEquals(0, review.run(new String[]{"--repo", wt.toString(), "--base", "day4", "--head", "demo/drop-approval-check",
                "--dry-run", "--out", rv.resolve("d").toString()}));
        String prompt = Files.readString(rv.resolve("d").resolve("prompt.txt"));
        assertTrue(prompt.contains("-    if not a or a[\"decision\"] != \"approve\":"), prompt);
    }

    @Test
    void aMissingWorktreeSaysHowToMakeOne() {
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        int code = new DemoPrs(GIT_ENV, new PrintStream(new ByteArrayOutputStream()))
                .run(new String[]{"--worktree", tmp.resolve("nope").toString()}, new PrintStream(err, true, StandardCharsets.UTF_8));
        assertEquals(1, code);
        assertTrue(err.toString().contains("is not a worktree: git worktree add"));
    }
}
