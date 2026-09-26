package com.airamatrix.day4.lab52;

import static org.junit.jupiter.api.Assertions.assertNotEquals;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;

/**
 * One real case end to end: python3 aira-ops, the gateway, GatewayAgentRunner, the graders.
 * Costs money (about $0.05-0.15), so it only runs with LAB_LIVE=1, never by default.
 */
@EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
class LiveEvalTest {
    @TempDir Path tmp;

    @Test
    void one_live_case_produces_a_verdict() throws Exception {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream out = new PrintStream(buf, true, StandardCharsets.UTF_8);
        RunEvals.Setup live = RunEvals.liveSetup(System.getenv());
        RunEvals.Setup inTmp = new RunEvals.Setup() {
            @Override public EvalHarness.RunnerFactory runners() { return live.runners(); }
            @Override public EvalHarness.OpsHandle startOps() throws Exception { return live.startOps(); }
            @Override public Path resultsDir() { return tmp; }
        };
        int code = RunEvals.main(new String[]{"--cases", "sso-no-config", "--budget", "0.5"}, out, System.err, System.getenv(), inTmp);
        System.out.println(buf.toString(StandardCharsets.UTF_8));
        assertNotEquals(2, code, "the live run could not run - check .env and python3");
    }
}
