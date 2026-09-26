package com.airamatrix.capstone.runtime;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenResponse;

/**
 * AgentCore Identity (agentcore/06-agents-java IdentityTokens, unchanged in substance). The Runtime gives each
 * request a workload access token (only when the caller passed runtimeUserId); the token vault exchanges it for
 * an OAuth token from the credential provider named at deploy time - here the INVESTIGATOR's, whose scope is
 * read-only, so Cedar shows this agent only ops-read tools. No client secret reaches this code or the image.
 */
public class IdentityTokens {
    private final BedrockAgentCoreClient client;
    private final String provider;
    private final List<String> scopes;
    private String cached;
    private Instant expires = Instant.EPOCH;

    public IdentityTokens(BedrockAgentCoreClient client, String provider, List<String> scopes) {
        this.client = client;
        this.provider = provider;
        this.scopes = scopes;
    }

    public synchronized String gatewayToken(String workloadAccessToken) {
        if (cached != null && Instant.now().isBefore(expires)) return cached;
        if (workloadAccessToken == null || workloadAccessToken.isBlank()) {
            throw new IllegalStateException("no workload access token on this request - invoke the runtime with runtimeUserId");
        }
        GetResourceOauth2TokenResponse r = client.getResourceOauth2Token(GetResourceOauth2TokenRequest.builder()
                .workloadIdentityToken(workloadAccessToken).resourceCredentialProviderName(provider)
                .scopes(scopes).oauth2Flow("M2M").build());
        if (r.accessToken() == null) throw new IllegalStateException("token vault returned no access token for " + provider);
        cached = r.accessToken();
        expires = Instant.now().plus(Duration.ofMinutes(50));
        return cached;
    }
}
