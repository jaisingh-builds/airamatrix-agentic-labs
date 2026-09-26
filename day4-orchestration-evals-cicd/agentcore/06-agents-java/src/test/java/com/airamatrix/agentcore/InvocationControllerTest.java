package com.airamatrix.agentcore;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** The AgentCore Runtime HTTP contract, with the agent itself mocked. */
@WebMvcTest(InvocationController.class)
@ActiveProfiles("test")
class InvocationControllerTest {

    @Autowired MockMvc mvc;
    @MockitoBean AgentService agent;

    @Test
    void ping_reports_healthy() throws Exception {
        mvc.perform(get("/ping")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("Healthy"));
    }

    @Test
    void invocations_accept_any_content_type_and_pass_session_and_workload_token() throws Exception {
        when(agent.invoke(eq("Investigate ticket T-1001"), eq("ops-team"), eq("sess-1"), eq("wat-abc")))
                .thenReturn(Map.of("role", "investigator", "result", "ok", "stop_reason", "end_turn", "tools_used", List.of()));
        mvc.perform(post("/invocations").contentType("application/octet-stream")
                        .header(InvocationController.SESSION, "sess-1").header(InvocationController.WAT, "wat-abc")
                        .content("{\"prompt\": \"Investigate ticket T-1001\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.result").value("ok"));
        verify(agent).invoke("Investigate ticket T-1001", "ops-team", "sess-1", "wat-abc");
    }

    @Test
    void the_older_workload_token_header_is_accepted_too() throws Exception {
        when(agent.invoke(eq("p"), eq("a1"), eq("s"), eq("old-wat"))).thenReturn(Map.of("result", "ok"));
        mvc.perform(post("/invocations").header(InvocationController.SESSION, "s").header(InvocationController.WAT_OLD, "old-wat")
                .content("{\"prompt\": \"p\", \"actor_id\": \"a1\"}")).andExpect(status().isOk());
    }

    @Test
    void a_missing_prompt_or_bad_json_is_a_400() throws Exception {
        mvc.perform(post("/invocations").content("{}")).andExpect(status().isBadRequest());
        mvc.perform(post("/invocations").content("not json")).andExpect(status().isBadRequest());
    }

    @Test
    void no_workload_token_is_explained_not_crashed() throws Exception {
        when(agent.invoke(eq("p"), eq("ops-team"), eq("s"), eq(null)))
                .thenThrow(new IllegalStateException("no workload access token on this request - invoke the runtime with runtimeUserId"));
        mvc.perform(post("/invocations").header(InvocationController.SESSION, "s").content("{\"prompt\": \"p\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("no workload access token on this request - invoke the runtime with runtimeUserId"));
    }
}
