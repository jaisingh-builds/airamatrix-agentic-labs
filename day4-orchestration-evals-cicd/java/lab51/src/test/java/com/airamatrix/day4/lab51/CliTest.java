package com.airamatrix.day4.lab51;

import static com.airamatrix.day4.lab51.FakeRunner.APPROVE;
import static com.airamatrix.day4.lab51.FakeRunner.GOOD;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.AgentRunner.RunnerException;

/** The CLI's words: the README walkthrough, command by command, with a fake runner and the aira-ops stub. */
class CliTest {

    @TempDir Path tmp;
    AiraOpsStub ops;
    Map<String, String> env;
    FakeRunner runner;
    String out, err;

    @BeforeEach
    void setUp() throws Exception {
        ops = new AiraOpsStub();
        env = new HashMap<>();
        env.put("PIPELINE_DB", tmp.resolve("cli.sqlite").toString());
        env.put("AIRA_OPS_URL", ops.url);
        runner = new FakeRunner();
    }

    @AfterEach
    void tearDown() { ops.close(); }

    int cli(String... args) {
        ByteArrayOutputStream o = new ByteArrayOutputStream(), e = new ByteArrayOutputStream();
        int code = new Cli(new PrintStream(o, true, StandardCharsets.UTF_8), new PrintStream(e, true, StandardCharsets.UTF_8),
                env::get, (url, tok) -> runner).main(args);
        out = o.toString(StandardCharsets.UTF_8);
        err = e.toString(StandardCharsets.UTF_8);
        return code;
    }

    String replayed() {
        assertEquals(0, cli("replay", "fixtures/blocked-36cc478fce.json"), err);
        Matcher m = Pattern.compile("^run (\\w+) replayed from fixtures/blocked-36cc478fce.json$", Pattern.MULTILINE).matcher(out);
        assertTrue(m.find(), out);
        return m.group(1);
    }

    @Test
    void the_replayed_blocked_run_walkthrough_refuses_at_every_step() {
        String rid = replayed();
        assertTrue(out.contains("run " + rid + " · ACC-1001 · needs_rework · $0.0"), out);
        assertTrue(out.contains("[investigate] done · attempt 1 · 0 tool calls · $0.0"), out);
        assertTrue(out.contains("\"verdict\": \"block\""), out);

        assertEquals(1, cli("approve", rid, "--by", "Your Name", "--reason", "backlog is P1"));
        assertEquals("refused: the reviewer blocked this proposal; approving it needs --override and a reason\n", err);

        assertEquals(0, cli("reject", rid, "--by", "Your Name", "--reason", "reviewer is right"));
        assertTrue(out.contains("[gate] reject by Your Name: reviewer is right"), out);

        assertEquals(1, cli("apply", rid));
        assertTrue(err.startsWith("AIRA_OPS_APPLY_TOKEN is not set - the apply step has its own credential"), err);

        env.put("AIRA_OPS_APPLY_TOKEN", AiraOpsStub.WRITE_TOKEN);
        assertEquals(1, cli("apply", rid));
        assertEquals("refused: run " + rid + " has no approval on record\n", err);
        assertEquals(0, ops.writesApplied.get());
    }

    @Test
    void an_override_is_recorded_and_applied_once() {
        String rid = replayed();
        assertEquals(0, cli("approve", rid, "--by", "Jai", "--reason", "memory fix confirmed on T-1001", "--override"));
        assertTrue(out.contains("[gate] approve by Jai (OVERRIDE): memory fix confirmed on T-1001"), out);
        assertEquals(1, cli("reject", rid, "--by", "Asha", "--reason", "too late"));
        assertEquals("refused: run " + rid + " was already decided\n", err);

        env.put("AIRA_OPS_APPLY_TOKEN", AiraOpsStub.WRITE_TOKEN);
        assertEquals(0, cli("apply", rid), err);
        assertTrue(out.contains("run " + rid + " · ACC-1001 · applied · $0.0"), out);
        assertTrue(Pattern.compile("\\[apply] done · op [0-9a-f-]{36} · update_config \\{\"action\": \"update_config\", "
                + "\"key\": \"ingest.max_concurrent_jobs\", \"value\": 16, \"expected_version\": 1, ").matcher(out).find(), out);
        assertEquals(0, cli("apply", rid));                                    // again: a no-op
        assertEquals(1, ops.writesApplied.get());
        assertEquals(16, ops.config.get("ingest.max_concurrent_jobs").value().asInt());
    }

    @Test
    void run_refuses_to_start_agents_while_the_write_token_is_set() {
        env.put("AIRA_OPS_READ_TOKEN", AiraOpsStub.READ_TOKEN);
        env.put("AIRA_OPS_APPLY_TOKEN", AiraOpsStub.WRITE_TOKEN);
        assertEquals(1, cli("run", "--account", "ACC-1001", "--question", "q"));
        assertTrue(err.startsWith("refusing to start the agents: AIRA_OPS_APPLY_TOKEN is set in this process."), err);
        assertTrue(runner.calls.isEmpty());
        assertEquals(0, cli("list"));
        assertEquals("", out, "no run row is created");
    }

    @Test
    void run_needs_the_read_token() {
        assertEquals(1, cli("run", "--account", "ACC-1001", "--question", "q"));
        assertTrue(err.startsWith("AIRA_OPS_READ_TOKEN is not set - agents get a read-only caller token ("), err);
    }

    @Test
    void a_failed_stage_says_how_to_retry_and_resume_does_not_re_pay() {
        env.put("AIRA_OPS_READ_TOKEN", AiraOpsStub.READ_TOKEN);
        runner.on("investigate", GOOD).on("review", new RunnerException("review: structured output failed", 0.11, 3), APPROVE);
        assertEquals(1, cli("run", "--account", "ACC-1001", "--question", "Ingest backlog on T-1001"));
        Matcher m = Pattern.compile("^run (\\w+) started$", Pattern.MULTILINE).matcher(out);
        assertTrue(m.find(), out);
        String rid = m.group(1);
        assertEquals("stage failed (recorded, finished stages kept): review: structured output failed\n"
                + "  trace:  python3 day4-orchestration-evals-cicd/common/trace_view.py --latest lab5-1-" + rid + "\n"
                + "  retry:  " + Cli.PROG + " resume " + rid + "\n", err);

        assertEquals(0, cli("resume", rid), err);
        assertTrue(out.contains("run " + rid + " · ACC-1001 · awaiting_approval · $0.17"), out);
        assertTrue(out.contains("[review] done · attempt 2 · 1 tool calls · $0.14"), out);
        assertEquals(java.util.List.of("investigate", "review", "review"), runner.calls);

        assertEquals(0, cli("approve", rid, "--by", "Jai", "--reason", "halfway step"));
        assertEquals(0, cli("resume", rid));
        assertTrue(out.endsWith("next: " + Cli.PROG + " apply " + rid + "   (in a shell that holds AIRA_OPS_APPLY_TOKEN)\n"), out);
    }

    @Test
    void list_prints_one_line_per_run() {
        String rid = replayed();
        assertEquals(0, cli("list"));
        assertEquals(String.format("%s  ACC-1001  %-18s $%-7s %s%n", rid, "needs_rework", "0.0",
                "Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked.".substring(0, 60)), out);
    }

    @Test
    void an_unknown_run_is_refused_like_python_keyerror() {
        assertEquals(1, cli("show", "nope"));
        assertEquals("refused: 'no run nope'\n", err);
    }

    @Test
    void missing_arguments_are_a_usage_error() {
        assertEquals(2, cli("run", "--account", "ACC-1001"));
        assertTrue(err.contains("lab51 run: error: the following arguments are required: --question"), err);
        assertEquals(2, cli("approve", "abc", "--by", "Jai"));
        assertTrue(err.contains("the following arguments are required: --reason"), err);
        assertEquals(2, cli("frobnicate"));
        assertTrue(err.contains("invalid choice: 'frobnicate'"), err);
    }
}
