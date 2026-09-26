package com.airamatrix.day4.lab51;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.GatewayAgentRunner;

/**
 * Costs money: two real agent stages through the gateway against a running aira-ops.
 * Runs only with LAB_LIVE=1 and AIRA_OPS_READ_TOKEN set (and no write token in the shell).
 */
@EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
@EnabledIfEnvironmentVariable(named = "AIRA_OPS_READ_TOKEN", matches = ".+")
class LiveTest {

    @TempDir Path tmp;

    @Test
    void a_live_run_stops_at_the_gate_or_ends_without_a_change() {
        try (Store store = new Store(tmp.resolve("live.sqlite").toString())) {
            Pipeline p = new Pipeline(store);
            var runner = GatewayAgentRunner.fromEnv(Pipeline.opsUrl(System::getenv), System.getenv("AIRA_OPS_READ_TOKEN"), 0.40);
            String rid = store.createRun("ACC-1001", "Ingest backlog on T-1001: slides queued since 06:00, pathologists blocked.");
            String status = p.advance(runner, rid).status();
            assertTrue(List.of("awaiting_approval", "needs_rework", "no_change").contains(status), status);
            assertTrue(store.operation(rid) == null, "a run never writes");
        }
    }
}
