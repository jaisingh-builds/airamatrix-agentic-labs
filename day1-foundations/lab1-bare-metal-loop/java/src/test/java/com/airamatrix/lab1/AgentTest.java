package com.airamatrix.lab1;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Lab 1.1 checks. Offline checks always run; live model checks need LAB_LIVE=1. */
class AgentTest {

    @Test
    void readFileReturnsContents() {
        Object[] r = Tools.dispatch("read_file", Map.of("path", "limits.txt"));
        assertTrue((Boolean) r[1]);
        assertTrue(((String) r[0]).contains("max_queue_depth"));
    }

    @Test
    void readFileErrorIsRecoverable() {
        Object[] r = Tools.dispatch("read_file", Map.of("path", "nope.txt"));
        assertFalse((Boolean) r[1]);
        assertTrue(((String) r[0]).contains("Available files"),
            "error must tell the agent how to recover");
    }

    @Test
    void readFileCannotEscapeWorkspace() {
        Object[] r = Tools.dispatch("read_file", Map.of("path", "../../../etc/passwd"));
        assertFalse((Boolean) r[1]);
    }

    @Test
    void calculatorComputes() {
        assertEquals("62.4", Tools.dispatch("calculator",
            Map.of("expression", "(812-500)/500*100"))[0]);
    }

    @Test
    void calculatorRejectsCode() {
        Object[] r = Tools.dispatch("calculator", Map.of("expression", "System.exit(1)"));
        assertFalse((Boolean) r[1]);
    }

    @Test
    void calculatorRejectsDivisionByZero() {
        Object[] r = Tools.dispatch("calculator", Map.of("expression", "1/0"));
        assertFalse((Boolean) r[1]);
    }

    @Test
    void httpGetBlocksOtherHosts() {
        Object[] r = Tools.dispatch("http_get", Map.of("url", "http://example.com/"));
        assertFalse((Boolean) r[1]);
        assertTrue(((String) r[0]).contains("not allowed"));
    }

    @Test
    void unknownToolIsReported() {
        assertFalse((Boolean) Tools.dispatch("definitely_not_a_tool", Map.of())[1]);
    }

    @Test
    void everySchemaIsWellFormed() {
        for (Map<String, Object> schema : Tools.schemas()) {
            assertNotNull(schema.get("name"));
            assertTrue(((String) schema.get("description")).length() > 40,
                schema.get("name") + ": description is the prompt - make it count");
            @SuppressWarnings("unchecked")
            Map<String, Object> input = (Map<String, Object>) schema.get("input_schema");
            assertEquals("object", input.get("type"));
            @SuppressWarnings("unchecked")
            Map<String, Map<String, Object>> props =
                (Map<String, Map<String, Object>>) input.get("properties");
            props.values().forEach(p -> assertNotNull(p.get("description"),
                "every parameter needs a description"));
        }
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
    void agentAnswersTheCapacityQuestion() {
        FixtureServer.serveInBackground();
        String answer = Agent.runAgent(
            "Fetch http://127.0.0.1:8137/status.json, read limits.txt, and say whether "
          + "the service is over capacity and by what percentage.", null, false);
        assertTrue(answer.replace("%", "").contains("62.4"),
            "should compute 62.4% via the calculator, got: " + answer);
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
    void stepLimitHaltsBeforeFinishing() {
        FixtureServer.serveInBackground();
        assertThrows(Agent.StepLimitExceeded.class, () ->
            Agent.runAgent("Read limits.txt and tell me the max_queue_depth value.", 1, false));
    }
}
