package com.airamatrix.capstone.runtime;

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
 * AgentCore Identity. The Runtime gives each request a workload access token (only when the caller passed
 * runtimeUserId); the token vault exchanges it for an OAuth token from the credential provider named at deploy
 * time - here the INVESTIGATOR's, whose scope is read-only, so Cedar shows this agent only ops-read tools.
 * No client secret reaches this code or the image.
 *
 * Every request must bring its own workload token, checked BEFORE any cache, and a cached Gateway token is reused
 * only for the SAME workload token (flagged by the PR reviewer: a cache checked first would let a later request
 * without a token - or with another caller's - ride on the previous caller's identity for 50 minutes).
 */
public class IdentityTokens {
    private final Function<GetResourceOauth2TokenRequest, GetResourceOauth2TokenResponse> exchange;
    private final String provider;
    private final List<String> scopes;
    private String cached, cachedFor;
    private Instant expires = Instant.EPOCH;

    public IdentityTokens(BedrockAgentCoreClient client, String provider, List<String> scopes) {
        this(client == null ? null : client::getResourceOauth2Token, provider, scopes);
    }

    public IdentityTokens(Function<GetResourceOauth2TokenRequest, GetResourceOauth2TokenResponse> exchange, String provider, List<String> scopes) {
        this.exchange = exchange;
        this.provider = provider;
        this.scopes = scopes;
    }

    public synchronized String gatewayToken(String workloadAccessToken) {
        if (workloadAccessToken == null || workloadAccessToken.isBlank()) {
            throw new IllegalStateException("no workload access token on this request - invoke the runtime with runtimeUserId");
        }
        String key = sha256(workloadAccessToken);
        if (cached != null && key.equals(cachedFor) && Instant.now().isBefore(expires)) return cached;
        GetResourceOauth2TokenResponse r = exchange.apply(GetResourceOauth2TokenRequest.builder()
                .workloadIdentityToken(workloadAccessToken).resourceCredentialProviderName(provider)
                .scopes(scopes).oauth2Flow("M2M").build());
        if (r.accessToken() == null) throw new IllegalStateException("token vault returned no access token for " + provider);
        cached = r.accessToken();
        cachedFor = key;
        expires = Instant.now().plus(Duration.ofMinutes(50));    // Cognito M2M tokens last 60 minutes
        return cached;
    }

    static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) { throw new IllegalStateException(e); }
    }
}
