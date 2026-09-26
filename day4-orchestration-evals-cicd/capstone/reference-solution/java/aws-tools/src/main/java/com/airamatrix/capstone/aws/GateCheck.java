package com.airamatrix.capstone.aws;

import static com.airamatrix.capstone.aws.AwsEnv.say;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.services.cognitoidentityprovider.CognitoIdentityProviderClient;

/**
 * The AgentCore-native half of the approval point: prove at the Gateway that the AGENT's identity (the
 * investigator client this runtime uses) cannot write, whatever a prompt says. tools/list shows it no write tool,
 * and a direct tools/call ops-write___add_ticket_comment is DENIED by Cedar - nothing is written. The client secret
 * is read from Cognito into memory for this one token request and never printed or stored.
 */
public final class GateCheck {
    private GateCheck() {}

    public static int run(AwsEnv env) throws Exception {
        String cid = env.need("clients.investigator.client_id").asText();
        String scope = String.join(" ", env.investigatorScopes());
        String secret;
        try (CognitoIdentityProviderClient cog = CognitoIdentityProviderClient.builder().region(env.awsRegion()).build()) {
            secret = cog.describeUserPoolClient(b -> b.userPoolId(env.need("user_pool").asText()).clientId(cid)).userPoolClient().clientSecret();
        }
        HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(20)).build();
        String basic = Base64.getEncoder().encodeToString((cid + ":" + secret).getBytes(StandardCharsets.UTF_8));
        HttpResponse<String> tok = http.send(HttpRequest.newBuilder(URI.create(env.need("token_url").asText())).timeout(Duration.ofSeconds(20))
                .header("Authorization", "Basic " + basic).header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString("grant_type=client_credentials&scope=" + URLEncoder.encode(scope, StandardCharsets.UTF_8)))
                .build(), HttpResponse.BodyHandlers.ofString());
        if (tok.statusCode() != 200) throw new IllegalStateException("token endpoint said " + tok.statusCode());
        String token = Contracts.JSON.readTree(tok.body()).path("access_token").asText();
        say("identity the agent's own client (investigator), scope '" + scope + "'");

        JsonNode list = rpc(http, env.need("gateway_url").asText(), token, Map.of("jsonrpc", "2.0", "id", 1, "method", "tools/list", "params", Map.of()));
        List<String> names = new ArrayList<>();
        list.path("result").path("tools").forEach(t -> names.add(t.path("name").asText()));
        long writes = names.stream().filter(n -> n.startsWith("ops-write___")).count();
        say("tools    tools/list shows " + names.size() + " tools, " + writes + " write tools");

        JsonNode call = rpc(http, env.need("gateway_url").asText(), token, Map.of("jsonrpc", "2.0", "id", 2, "method", "tools/call",
                "params", Map.of("name", "ops-write___add_ticket_comment", "arguments", Map.of("ticket_id", "T-1001",
                        "comment", "[capstone gate-check] this call must be denied", "Idempotency-Key", UUID.randomUUID().toString()))));
        if (call.has("error") || call.path("result").path("isError").asBoolean(false)) {
            String msg = call.has("error") ? call.path("error").path("message").asText() : call.path("result").path("content").path(0).path("text").asText();
            say("DENIED   " + (msg.length() > 220 ? msg.substring(0, 220) : msg));
            return writes == 0 ? 0 : 1;
        }
        say("UNEXPECTED ALLOW - the agent identity could write through the Gateway: " + call.path("result").toString());
        return 1;
    }

    static JsonNode rpc(HttpClient http, String url, String token, Map<String, Object> body) throws Exception {
        HttpResponse<String> r = http.send(HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(60))
                .header("Authorization", "Bearer " + token).header("Content-Type", "application/json")
                .header("Accept", "application/json, text/event-stream")
                .POST(HttpRequest.BodyPublishers.ofString(Contracts.JSON.writeValueAsString(body))).build(), HttpResponse.BodyHandlers.ofString());
        String raw = r.body();
        int start = raw.indexOf('{'), end = raw.lastIndexOf('}');       // the body may be SSE-framed
        if (start < 0) return Contracts.object().put("error", "HTTP " + r.statusCode());
        return Contracts.JSON.readTree(raw.substring(start, end + 1));
    }
}
