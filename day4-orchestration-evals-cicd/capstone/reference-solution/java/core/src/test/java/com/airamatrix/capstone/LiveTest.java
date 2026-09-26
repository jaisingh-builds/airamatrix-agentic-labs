package com.airamatrix.capstone;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.util.List;

import org.junit.jupiter.api.Test;

import com.airamatrix.labkit.Config;
import com.airamatrix.labkit.GatewayClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * One real run through the training gateway (about $0.03). Runs only with LAB_LIVE=1 - a live test costs money
 * and never runs by default. Unset AIRA_OPS_TOKEN / AIRA_OPS_APPLY_TOKEN first: the agent refuses to start with them.
 *   LAB_LIVE=1 mvn -q -f java/pom.xml -pl core -am test -Dtest=LiveTest -Dsurefire.failIfNoSpecifiedTests=false
 */
class LiveTest {
    @Test
    void theBacklogCasePassesItsGoldenChecksLive() throws Exception {
        assumeTrue("1".equals(System.getenv("LAB_LIVE")), "LAB_LIVE=1 not set - live test skipped");
        JsonNode golden = Evals.load(Repo.solution().resolve("golden/cases.json"));
        JsonNode kase = Evals.select(golden, "backlog-acc1001").get(0);
        Config cfg = new Config().require();
        try (PrivateOps ops = PrivateOps.start(List.of("ACC-1001"), false)) {
            ObjectNode r = Evals.local(ops, Evals.localAgents(new GatewayClient(cfg)::messages, cfg.model, 10), 0.30).run(kase);
            assertTrue(!r.has("error"), String.valueOf(r.path("error")));
            assertEquals("awaiting_approval", r.path("status").asText(), r.toPrettyString());
            ObjectNode g = Checks.gradeCase(kase, r);
            assertTrue(g.path("passed").asBoolean(), g.toPrettyString());
        }
    }
}
