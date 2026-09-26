package com.airamatrix.agentcore.tools;

import static com.airamatrix.agentcore.tools.Env.say;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.services.cognitoidentityprovider.CognitoIdentityProviderClient;

/**
 * Step 7b in Java: 07-run/approve.py - the HUMAN approver applies the change the agents asked for.
 *
 *   approve --value 16 --version 1                    permitted: approver scope, value <= 16
 *   approve --value 32 --version 1                    denied by the hard_ceiling forbid policy
 *   approve --as supervisor --value 16 --version 1    an agent's client: denied
 *
 * The approver's secret lives only in out/approver.json (step 3, mode 0600) - never printed, never in a runtime.
 */
public final class Approve {
    public record Request(int value, int version, String who) {}

    public static Request parse(List<String> args) {
        Integer value = null, version = null;
        String who = "approver";
        for (int i = 0; i < args.size(); i++) {
            String a = args.get(i);
            if (i + 1 >= args.size()) throw new IllegalArgumentException(a + " needs a value");
            String v = args.get(++i);
            switch (a) {
                case "--value" -> value = Integer.parseInt(v);
                case "--version" -> version = Integer.parseInt(v);
                case "--as" -> who = v;
                default -> throw new IllegalArgumentException("unknown option " + a);
            }
        }
        if (value == null || version == null) throw new IllegalArgumentException("usage: approve --value N --version N [--as approver|supervisor|investigator]");
        if (!List.of("approver", "supervisor", "investigator").contains(who)) throw new IllegalArgumentException("--as must be approver, supervisor or investigator");
        return new Request(value, version, who);
    }

    public static void run(Env env, State state, Request req) throws Exception {
        String gw = state.need("gateway_url").asText(), tokenUrl = state.need("token_url").asText();
        String cid, secret, scope;
        if (req.who().equals("approver")) {
            JsonNode c = State.JSON.readTree(Files.readString(state.path.getParent().resolve("approver.json")));
            cid = c.path("client_id").asText();
            secret = c.path("client_secret").asText();
            scope = c.path("scope").asText();
        } else {   // the agent's own client, to show the policy denies it even with valid credentials
            JsonNode c = state.need("clients").path(req.who());
            cid = c.path("client_id").asText();
            List<String> scopes = new ArrayList<>();
            c.path("scopes").forEach(s -> scopes.add(s.asText()));
            scope = String.join(" ", scopes);
            try (CognitoIdentityProviderClient cog = CognitoIdentityProviderClient.builder().region(env.awsRegion()).build()) {
                secret = cog.describeUserPoolClient(b -> b.userPoolId(state.need("user_pool").asText()).clientId(cid))
                        .userPoolClient().clientSecret();
            }
        }
        HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(20)).build();
        String basic = Base64.getEncoder().encodeToString((cid + ":" + secret).getBytes(StandardCharsets.UTF_8));
        HttpResponse<String> tok = http.send(HttpRequest.newBuilder(URI.create(tokenUrl)).timeout(Duration.ofSeconds(20))
                .header("Authorization", "Basic " + basic).header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString("grant_type=client_credentials&scope=" + URLEncoder.encode(scope, StandardCharsets.UTF_8)))
                .build(), HttpResponse.BodyHandlers.ofString());
        if (tok.statusCode() != 200) throw new IllegalStateException("token endpoint said " + tok.statusCode());
        String token = State.JSON.readTree(tok.body()).path("access_token").asText();
        say("identity " + req.who() + ": token with scope '" + scope + "'");

        Map<String, Object> body = Map.of("jsonrpc", "2.0", "id", 1, "method", "tools/call", "params", Map.of(
                "name", "ops-write___set_ingest_concurrency",
                "arguments", Map.of("value", req.value(), "expected_version", req.version(), "Idempotency-Key", UUID.randomUUID().toString())));
        HttpResponse<String> r = http.send(HttpRequest.newBuilder(URI.create(gw)).timeout(Duration.ofSeconds(60))
                .header("Authorization", "Bearer " + token).header("Content-Type", "application/json")
                .header("Accept", "application/json, text/event-stream")
                .POST(HttpRequest.BodyPublishers.ofString(State.JSON.writeValueAsString(body))).build(), HttpResponse.BodyHandlers.ofString());
        say(outcome(r.body()));
    }

    /** One line: DENIED (Gateway/Cedar), REJECTED (aira-ops said no) or APPLIED. The body may be SSE-framed. */
    static String outcome(String raw) throws Exception {
        String json = raw.startsWith("{") ? raw : raw.substring(raw.indexOf('{'));
        int end = json.lastIndexOf('}');
        JsonNode msg = State.JSON.readTree(json.substring(0, end + 1));
        if (msg.has("error")) return "DENIED   " + cut(msg.path("error").path("message").asText(), 220);
        JsonNode res = msg.path("result");
        String text = res.path("content").path(0).path("text").asText();
        return res.path("isError").asBoolean() ? "REJECTED by aira-ops: " + cut(text, 300) : "APPLIED  " + cut(text, 300);
    }

    static String cut(String s, int n) { return s.length() <= n ? s : s.substring(0, n); }

}
