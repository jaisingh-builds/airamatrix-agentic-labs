package com.airamatrix.agentcore;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;

import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenResponse;

/** The Gateway token is bound to the workload token that obtained it. */
class IdentityTokensTest {
    final List<String> exchanged = new ArrayList<>();
    final IdentityTokens tokens = new IdentityTokens((GetResourceOauth2TokenRequest r) -> {
        exchanged.add(r.workloadIdentityToken());
        return GetResourceOauth2TokenResponse.builder().accessToken("gw-for-" + r.workloadIdentityToken()).build();
    }, "aira-d4-investigator", List.of("aira-ops/read"));

    @Test
    void the_same_workload_token_reuses_the_cached_gateway_token() {
        assertEquals("gw-for-wat-a", tokens.gatewayToken("wat-a"));
        assertEquals("gw-for-wat-a", tokens.gatewayToken("wat-a"));
        assertEquals(List.of("wat-a"), exchanged);
    }

    @Test
    void a_request_without_a_workload_token_is_refused_even_when_a_token_is_cached() {
        tokens.gatewayToken("wat-a");
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> tokens.gatewayToken(null));
        assertTrue(e.getMessage().startsWith("no workload access token on this request"));
        assertThrows(IllegalStateException.class, () -> tokens.gatewayToken("  "));
    }

    @Test
    void another_workload_token_gets_its_own_exchange_never_the_previous_callers_token() {
        tokens.gatewayToken("wat-a");
        assertEquals("gw-for-wat-b", tokens.gatewayToken("wat-b"));
        assertEquals(List.of("wat-a", "wat-b"), exchanged);
    }
}
