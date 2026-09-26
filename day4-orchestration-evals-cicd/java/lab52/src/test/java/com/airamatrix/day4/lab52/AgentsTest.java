package com.airamatrix.day4.lab52;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Map;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.Test;

/** The eval must run the prompt the pipeline runs: pinned against lab5-1-handoff/agents.py. */
class AgentsTest {

    /** The value of NAME = ("..." "...") in agents.py: its string literals, joined. */
    static String pythonConstant(String src, String name) {
        Matcher block = Pattern.compile(name + " = \\((.*?)\\)\\n", Pattern.DOTALL).matcher(src);
        assertTrue(block.find(), name + " not found in agents.py");
        Matcher lit = Pattern.compile("\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(block.group(1));
        StringBuilder sb = new StringBuilder();
        while (lit.find()) sb.append(lit.group(1).replace("\\\"", "\"").replace("\\\\", "\\"));
        return sb.toString();
    }

    @Test
    void investigate_system_is_in_sync_with_lab51() throws Exception {
        String src = Files.readString(LabPaths.repo().resolve("day4-orchestration-evals-cicd/lab5-1-handoff/agents.py"),
                StandardCharsets.UTF_8);
        assertEquals(pythonConstant(src, "INVESTIGATE_SYSTEM"), Agents.INVESTIGATE_SYSTEM);
    }

    @Test
    void investigate_prompt_matches_lab51() {
        assertEquals("Account: ACC-1001\nReported problem: Backlog\n\nInvestigate and return your proposal.",
                Agents.investigatePrompt("ACC-1001", "Backlog"));
    }

    @Test
    void the_agent_sees_no_secret_but_the_gateway_key() {
        Map<String, String> env = Map.of("ANTHROPIC_AUTH_TOKEN", "gw", "AIRA_OPS_TOKEN", "admin",
                "AIRA_OPS_APPLY_TOKEN", "write", "GITHUB_TOKEN", "gh", "PATH", "/usr/bin");
        Function<String, String> seen = Agents.scrubbed(env);
        assertNull(seen.apply("AIRA_OPS_TOKEN"));
        assertNull(seen.apply("AIRA_OPS_APPLY_TOKEN"));
        assertNull(seen.apply("GITHUB_TOKEN"));
        assertEquals("gw", seen.apply("ANTHROPIC_AUTH_TOKEN"));
        assertEquals("/usr/bin", seen.apply("PATH"));
    }
}
