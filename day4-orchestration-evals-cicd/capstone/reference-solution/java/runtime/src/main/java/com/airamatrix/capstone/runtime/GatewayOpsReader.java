package com.airamatrix.capstone.runtime;

import java.net.URI;
import java.time.Duration;
import java.util.Map;
import java.util.stream.Collectors;

import com.airamatrix.capstone.OpsReader;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.client.transport.HttpClientStreamableHttpTransport;
import io.modelcontextprotocol.spec.McpSchema;

/**
 * AgentCore mode: the reads come from the AgentCore Gateway's ops-read tools over MCP (the official MCP Java SDK,
 * streamable HTTP), with this runtime's Identity token. Cedar decides which tools exist for this identity: with the
 * investigator's read scope, tools/list has no write tool at all. The Gateway's own aira-ops credential can read
 * EVERY account, so the tenant boundary is enforced in core (Sla, Tools), not assumed from the credential.
 */
public final class GatewayOpsReader implements OpsReader, AutoCloseable {
    static final String P = "ops-read___";
    private final McpSyncClient client;

    public GatewayOpsReader(String gatewayUrl, String bearerToken) {
        URI u = URI.create(gatewayUrl);
        HttpClientStreamableHttpTransport transport = HttpClientStreamableHttpTransport.builder(u.getScheme() + "://" + u.getAuthority())
                .endpoint(u.getPath().isEmpty() ? "/mcp" : u.getPath())
                .httpRequestCustomizer((b, method, uri, body, ctx) -> b.header("Authorization", "Bearer " + bearerToken))
                .build();
        this.client = McpClient.sync(transport).requestTimeout(Duration.ofSeconds(30)).build();
        client.initialize();
    }

    @Override public JsonNode account(String id) { return call("lookup_account", Map.of("account_id", id)); }
    @Override public JsonNode tickets(String accountId) { return call("search_tickets", Map.of("account_id", accountId, "limit", 50)); }
    @Override public JsonNode ticket(String id) { return call("get_ticket", Map.of("ticket_id", id)); }
    @Override public JsonNode jobs(String accountId) { return call("list_jobs", Map.of("account_id", accountId)); }
    @Override public JsonNode config(String key) { return call("get_config", Map.of("key", key)); }

    JsonNode call(String tool, Map<String, Object> args) {
        McpSchema.CallToolResult r;
        try {
            r = client.callTool(new McpSchema.CallToolRequest(P + tool, args));
        } catch (Exception e) {       // a Cedar denial arrives as a JSON-RPC error
            throw new OpsError(403, "denied", "gateway refused " + P + tool + ": " + String.valueOf(e.getMessage()));
        }
        String text = r.content() == null ? "" : r.content().stream()
                .map(c -> c instanceof McpSchema.TextContent tc ? tc.text() : "").collect(Collectors.joining());
        JsonNode body;
        try {
            body = text.isBlank() ? Contracts.object() : Contracts.JSON.readTree(text);
        } catch (Exception e) {
            throw new OpsError(502, "invalid", P + tool + " returned non-JSON");
        }
        JsonNode err = body.path("error");
        if (Boolean.TRUE.equals(r.isError()) || err.isObject()) {
            String code = err.path("code").asText("tool_error");
            throw new OpsError(code.equals("not_found") ? 404 : 502, code, err.path("message").asText(text.length() > 200 ? text.substring(0, 200) : text));
        }
        return body;
    }

    @Override public void close() { client.close(); }
}
