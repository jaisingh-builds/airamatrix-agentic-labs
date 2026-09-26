package com.airamatrix.capstone;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Local mode: aira-ops over HTTP with a READ-ONLY caller token scoped to one account
 * ({@code capstone.jar tokens}). aira-ops itself enforces the scope: another account's ticket is a 404.
 * Responses are bounded at the source - a body over {@link #MAX_BYTES} is refused, never sliced
 * (slicing JSON at a byte count produces invalid JSON).
 */
public final class HttpOpsReader implements OpsReader {
    static final int MAX_BYTES = 256 * 1024;
    private final String baseUrl, token;
    private final HttpClient http;
    private final Duration timeout;

    public HttpOpsReader(String baseUrl, String token) { this(baseUrl, token, Duration.ofSeconds(8)); }

    public HttpOpsReader(String baseUrl, String token, Duration timeout) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.token = token;
        this.timeout = timeout;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    }

    @Override public JsonNode account(String id) { return get("/accounts/" + enc(id)); }
    @Override public JsonNode tickets(String accountId) { return get("/tickets?limit=50&account_id=" + enc(accountId)); }
    @Override public JsonNode ticket(String id) { return get("/tickets/" + enc(id)); }
    @Override public JsonNode jobs(String accountId) { return get("/jobs?account_id=" + enc(accountId)); }
    @Override public JsonNode config(String key) { return get("/config/" + enc(key)); }

    private JsonNode get(String path) {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + path)).timeout(timeout)
                .header("Authorization", "Bearer " + token).header("X-Actor", "sla-responder").GET().build();
        try {
            HttpResponse<InputStream> r = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
            byte[] body;
            try (InputStream in = r.body()) { body = in.readNBytes(MAX_BYTES + 1); }
            if (body.length > MAX_BYTES) throw new OpsError(502, "too_large", "aira-ops response over " + MAX_BYTES + " bytes - refused, not truncated");
            JsonNode json = body.length == 0 ? Contracts.object() : Contracts.JSON.readTree(body);
            if (r.statusCode() >= 400) {
                JsonNode e = json.path("error");
                throw new OpsError(r.statusCode(), e.path("code").asText("http_" + r.statusCode()), e.path("message").asText("HTTP " + r.statusCode()));
            }
            return json;
        } catch (IOException e) {
            throw new OpsError(0, "unavailable", "aira-ops unreachable: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new OpsError(0, "interrupted", "interrupted");
        }
    }

    static String enc(String s) { return URLEncoder.encode(s, StandardCharsets.UTF_8); }
}
