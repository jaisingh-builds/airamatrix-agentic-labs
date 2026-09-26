package com.airamatrix.agentcore;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.function.Function;

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
 *
 * Every request must bring its own workload token, checked BEFORE the cache, and a cached Gateway token is
 * reused only for the SAME workload token. A cache checked first would let a later request with no token - or
 * with another caller's - act with the previous caller's identity for up to 50 minutes.
 */
public class IdentityTokens {
    private final Function<GetResourceOauth2TokenRequest, GetResourceOauth2TokenResponse> exchange;
    private final String provider;
    private final List<String> scopes;
    private String cached, cachedFor;
    private Instant expires = Instant.EPOCH;

    public IdentityTokens(BedrockAgentCoreClient client, String provider, List<String> scopes) {
        this(client::getResourceOauth2Token, provider, scopes);
    }

    /** Tests pass a fake token vault. */
    IdentityTokens(Function<GetResourceOauth2TokenRequest, GetResourceOauth2TokenResponse> exchange, String provider, List<String> scopes) {
        this.exchange = exchange;
        this.provider = provider;
        this.scopes = scopes;
    }

    public synchronized String gatewayToken(String workloadAccessToken) {
        if (workloadAccessToken == null || workloadAccessToken.isBlank()) {
            throw new IllegalStateException("no workload access token on this request - invoke the runtime with "
                    + "runtimeUserId (SigV4 callers must pass it for AgentCore Identity to work)");
        }
        String key = sha256(workloadAccessToken);                 // the token itself is not kept
        if (cached != null && key.equals(cachedFor) && Instant.now().isBefore(expires)) return cached;
        GetResourceOauth2TokenResponse r = exchange.apply(GetResourceOauth2TokenRequest.builder()
                .workloadIdentityToken(workloadAccessToken)
                .resourceCredentialProviderName(provider)
                .scopes(scopes)
                .oauth2Flow("M2M")
                .build());
        if (r.accessToken() == null) throw new IllegalStateException("token vault returned no access token for " + provider);
        cached = r.accessToken();
        cachedFor = key;
        expires = Instant.now().plus(Duration.ofMinutes(50));    // Cognito M2M tokens last 60 minutes
        return cached;
    }

    static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
