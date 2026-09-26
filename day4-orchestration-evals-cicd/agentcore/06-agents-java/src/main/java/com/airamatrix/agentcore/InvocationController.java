package com.airamatrix.agentcore;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * The AgentCore Runtime HTTP contract: GET /ping and POST /invocations on port 8080.
 * The Runtime adds the session id and - when the caller passed runtimeUserId - a workload access
 * token for this runtime's workload identity, which AgentCore Identity exchanges for an OAuth token.
 */
@RestController
public class InvocationController {
    static final String SESSION = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id";
    static final String WAT = "X-Amz-Bedrock-AgentCore-Identity-WAT";
    static final String WAT_OLD = "WorkloadAccessToken";

    private final AgentService agent;

    public InvocationController(AgentService agent) { this.agent = agent; }

    @GetMapping("/ping")
    public Map<String, Object> ping() {
        return Map.of("status", "Healthy", "time_of_last_update", Instant.now().getEpochSecond());
    }

    /** Any content type: InvokeAgentRuntime callers often omit it (application/octet-stream) - the body is JSON regardless. */
    @SuppressWarnings("unchecked")
    @PostMapping(value = "/invocations", consumes = MediaType.ALL_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> invocations(@RequestBody(required = false) byte[] body,
            @RequestHeader(value = SESSION, required = false) String session,
            @RequestHeader(value = WAT, required = false) String wat,
            @RequestHeader(value = WAT_OLD, required = false) String watOld) {
        Map<String, Object> payload;
        try {
            payload = body == null || body.length == 0 ? Map.of() : Json.MAPPER.readValue(body, Map.class);
        } catch (Exception e) {
            return ResponseEntity.badRequest().body(Map.of("error", "body must be a JSON object: {\"prompt\": \"...\"}"));
        }
        String prompt = String.valueOf(payload.getOrDefault("prompt", ""));
        if (prompt.isBlank()) return ResponseEntity.badRequest().body(Map.of("error", "prompt is required"));
        String actor = String.valueOf(payload.getOrDefault("actor_id", "ops-team"));
        String sid = session != null ? session : UUID.randomUUID().toString();
        try {
            return ResponseEntity.ok(agent.invoke(prompt, actor, sid, wat != null ? wat : watOld));
        } catch (IllegalStateException e) {
            return ResponseEntity.status(400).body(Map.of("error", e.getMessage()));
        }
    }
}
