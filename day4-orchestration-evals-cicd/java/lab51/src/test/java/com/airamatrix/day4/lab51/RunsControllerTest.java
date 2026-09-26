package com.airamatrix.day4.lab51;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Path;
import java.util.Map;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import com.airamatrix.day4.common.Contracts;

/** The REST API: same gate as the CLI, and the agents side and the apply side are separate processes. */
class RunsControllerTest {

    @TempDir Path tmp;
    Store store;
    AiraOpsStub ops;
    MockMvc agents, applier;
    FakeRunner runner = new FakeRunner();

    @BeforeEach
    void setUp() throws Exception {
        store = new Store(tmp.resolve("api.sqlite").toString());
        ops = new AiraOpsStub();
        agents = MockMvcBuilders.standaloneSetup(new RunsController(store,
                new ServeSettings(false, ops.url, AiraOpsStub.READ_TOKEN, ""), (u, t) -> runner)).build();
        applier = MockMvcBuilders.standaloneSetup(new RunsController(store,
                new ServeSettings(true, ops.url, "", AiraOpsStub.WRITE_TOKEN), (u, t) -> runner)).build();
    }

    @AfterEach
    void tearDown() {
        ops.close();
        store.close();
    }

    String replay() throws Exception {
        String body = agents.perform(post("/api/replay"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("needs_rework"))
                .andReturn().getResponse().getContentAsString();
        return Contracts.JSON.readTree(body).get("id").asText();
    }

    static String json(Object o) throws Exception { return Contracts.JSON.writeValueAsString(o); }

    @Test
    void the_gate_holds_over_http() throws Exception {
        String rid = replay();
        agents.perform(post("/api/runs/" + rid + "/approve").contentType(MediaType.APPLICATION_JSON)
                        .content(json(Map.of("by", "Jai", "reason", "backlog is P1"))))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("refused: the reviewer blocked this proposal; approving it needs --override and a reason"));
        agents.perform(post("/api/runs/" + rid + "/reject").contentType(MediaType.APPLICATION_JSON)
                        .content(json(Map.of("by", "Jai", "reason", "reviewer is right"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("rejected"))
                .andExpect(jsonPath("$.approval.approver").value("Jai"));
        applier.perform(post("/api/runs/" + rid + "/apply"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("refused: run " + rid + " has no approval on record"));
    }

    @Test
    void only_the_apply_server_writes_and_only_once() throws Exception {
        String rid = replay();
        agents.perform(post("/api/runs/" + rid + "/approve").contentType(MediaType.APPLICATION_JSON)
                        .content(json(Map.of("by", "Jai", "reason", "memory fix confirmed", "override", true))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.approval.override").value(true));
        agents.perform(post("/api/runs/" + rid + "/apply"))
                .andExpect(status().isForbidden());
        assertEquals(0, ops.writesApplied.get());
        applier.perform(post("/api/runs/" + rid + "/apply"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("applied"))
                .andExpect(jsonPath("$.operation.status").value("done"));
        applier.perform(post("/api/runs/" + rid + "/apply")).andExpect(status().isOk());
        assertEquals(1, ops.writesApplied.get());
    }

    @Test
    void the_apply_server_never_starts_a_stage() throws Exception {
        applier.perform(post("/api/replay")).andExpect(status().isForbidden());
        applier.perform(post("/api/runs").contentType(MediaType.APPLICATION_JSON)
                        .content(json(Map.of("account", "ACC-1001", "question", "q"))))
                .andExpect(status().isForbidden());
        assertTrue(runner.calls.isEmpty());
        assertTrue(store.runs().isEmpty());
    }

    @Test
    void post_runs_runs_both_stages_and_stops_at_the_gate() throws Exception {
        runner.on("investigate", FakeRunner.GOOD).on("review", FakeRunner.APPROVE);
        agents.perform(post("/api/runs").contentType(MediaType.APPLICATION_JSON)
                        .content(json(Map.of("account", "ACC-1001", "question", "Ingest backlog on T-1001"))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("awaiting_approval"))
                .andExpect(jsonPath("$.stages.review.output.verdict").value("approve"))
                .andExpect(jsonPath("$.cost_usd").value(0.06));
        agents.perform(get("/api/runs")).andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void an_unknown_run_is_404() throws Exception {
        agents.perform(get("/api/runs/nope"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("refused: 'no run nope'"));
    }

    @Test
    void servers_refuse_to_start_with_the_wrong_credentials() {
        assertTrue(ServeSettings.startupRefusal(false, Map.of("AIRA_OPS_APPLY_TOKEN", "x")::get)
                .startsWith("refusing to start `serve`: AIRA_OPS_APPLY_TOKEN is set"));
        assertTrue(ServeSettings.startupRefusal(false, Map.of("AIRA_OPS_TOKEN", "x")::get)
                .startsWith("refusing to start `serve`: AIRA_OPS_TOKEN is set"));
        assertNull(ServeSettings.startupRefusal(false, Map.of("AIRA_OPS_READ_TOKEN", "r")::get));
        assertTrue(ServeSettings.startupRefusal(true, Map.<String, String>of()::get)
                .startsWith("refusing to start `serve --apply`: AIRA_OPS_APPLY_TOKEN is not set"));
        assertNull(ServeSettings.startupRefusal(true, Map.of("AIRA_OPS_APPLY_TOKEN", "w")::get));
        // the apply server never reads the read token, the agents server never reads the apply token
        ServeSettings a = ServeSettings.fromEnv(false, Map.of("AIRA_OPS_READ_TOKEN", "r", "AIRA_OPS_APPLY_TOKEN", "w")::get);
        ServeSettings w = ServeSettings.fromEnv(true, Map.of("AIRA_OPS_READ_TOKEN", "r", "AIRA_OPS_APPLY_TOKEN", "w")::get);
        assertEquals("", a.applyToken());
        assertEquals("", w.readToken());
    }
}
