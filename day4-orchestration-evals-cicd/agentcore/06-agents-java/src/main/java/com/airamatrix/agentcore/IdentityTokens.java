package com.airamatrix.agentcore;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenResponse;

/**
 * AgentCore Identity, the Java way of Python's {@code @requires_access_token(auth_flow="M2M")}.
 *
 * The Runtime hands each request a WORKLOAD ACCESS TOKEN for this runtime's own workload identity
 * (header X-Amz-Bedrock-AgentCore-Identity-WAT - only when the caller passed runtimeUserId). The
 * token vault exchanges it for an OAuth token from the credential provider named at deploy time.
 * The Cognito client secret never reaches this code, the image or the environment.
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
            throw new IllegalStateException("no workload access token on this request - invoke the runtime with "
                    + "runtimeUserId (SigV4 callers must pass it for AgentCore Identity to work)");
        }
        GetResourceOauth2TokenResponse r = client.getResourceOauth2Token(GetResourceOauth2TokenRequest.builder()
                .workloadIdentityToken(workloadAccessToken)
                .resourceCredentialProviderName(provider)
                .scopes(scopes)
                .oauth2Flow("M2M")
                .build());
        if (r.accessToken() == null) throw new IllegalStateException("token vault returned no access token for " + provider);
        cached = r.accessToken();
        expires = Instant.now().plus(Duration.ofMinutes(50));    // Cognito M2M tokens last 60 minutes
        return cached;
    }
}
