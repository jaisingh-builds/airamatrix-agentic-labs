package com.airamatrix.labkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Set;

/** Anthropic Messages client over the training gateway. JDK HTTP client only. */
public final class GatewayClient {
    private static final Set<Integer> RETRYABLE = Set.of(429, 500, 502, 503, 504);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final Config cfg;
    private final HttpClient http;

    public GatewayClient(Config config) {
        this.cfg = config.require();
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(20)).build();
    }

    public JsonNode messages(List<?> messages, List<?> tools, String system, int maxTokens) {
        ObjectNode payload = MAPPER.createObjectNode();
        payload.put("model", cfg.model);
        payload.put("max_tokens", maxTokens);
        payload.set("messages", MAPPER.valueToTree(messages));
        if (tools != null && !tools.isEmpty()) payload.set("tools", MAPPER.valueToTree(tools));
        if (system != null && !system.isEmpty()) payload.put("system", system);

        GatewayError last = null;
        for (int attempt = 0; attempt < 3; attempt++) {
            try {
                HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(cfg.baseUrl + "/v1/messages"))
                    .timeout(Duration.ofSeconds(180))
                    .header("content-type", "application/json")
                    .header("anthropic-version", "2023-06-01")
                    .header("authorization", "Bearer " + cfg.apiKey)
                    .POST(HttpRequest.BodyPublishers.ofString(MAPPER.writeValueAsString(payload)))
                    .build();
                HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() == 200) return MAPPER.readTree(response.body());
                // 429 = your daily budget or rate limit at the gateway.
                if (RETRYABLE.contains(response.statusCode()) && attempt < 2) {
                    last = new GatewayError(response.statusCode(), response.body());
                    Thread.sleep(1000L << attempt);
                    continue;
                }
                throw new GatewayError(response.statusCode(), response.body());
            } catch (GatewayError e) {
                throw e;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException(e);
            } catch (Exception e) {
                if (attempt < 2) { last = new GatewayError(0, String.valueOf(e.getMessage())); continue; }
                throw new GatewayError(0, "cannot reach " + cfg.baseUrl + ": " + e.getMessage());
            }
        }
        throw last;
    }

    public static ObjectMapper mapper() { return MAPPER; }
}
