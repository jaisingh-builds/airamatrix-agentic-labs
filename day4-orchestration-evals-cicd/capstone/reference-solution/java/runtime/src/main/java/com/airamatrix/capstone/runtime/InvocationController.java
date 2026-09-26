package com.airamatrix.capstone.runtime;

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

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;

/**
 * The AgentCore Runtime HTTP contract: GET /ping and POST /invocations on 8080 (any content type - callers send
 * application/octet-stream). Payload: {"prompt", "actor_id", "account_id", "as_of", "run_id"?}. The caller binds
 * the tenant and the clock; the model never chooses them.
 */
@RestController
public class InvocationController {
    static final String SESSION = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id";
    static final String WAT = "X-Amz-Bedrock-AgentCore-Identity-WAT";
    static final String WAT_OLD = "WorkloadAccessToken";

    private final InvocationService service;

    public InvocationController(InvocationService service) { this.service = service; }

    @GetMapping("/ping")
    public Map<String, Object> ping() { return Map.of("status", "Healthy", "time_of_last_update", Instant.now().getEpochSecond()); }

    @SuppressWarnings("unchecked")
    @PostMapping(value = "/invocations", consumes = MediaType.ALL_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Object> invocations(@RequestBody(required = false) byte[] body,
            @RequestHeader(value = SESSION, required = false) String session,
            @RequestHeader(value = WAT, required = false) String wat,
            @RequestHeader(value = WAT_OLD, required = false) String watOld) {
        Map<String, Object> payload;
        try {
            payload = body == null || body.length == 0 ? Map.of() : Contracts.JSON.readValue(body, Map.class);
        } catch (Exception e) {
            return ResponseEntity.badRequest().body(Map.of("error", "body must be a JSON object"));
        }
        try {
            return ResponseEntity.ok(service.invoke(payload, session != null ? session : UUID.randomUUID().toString(), wat != null ? wat : watOld));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        } catch (IllegalStateException e) {
            return ResponseEntity.status(400).body(Map.of("error", Spans.redact(String.valueOf(e.getMessage()))));
        }
    }
}
