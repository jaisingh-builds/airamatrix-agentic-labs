package com.airamatrix.agentcore;

import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import io.modelcontextprotocol.client.McpClient;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.client.transport.HttpClientStreamableHttpTransport;
import io.modelcontextprotocol.spec.McpSchema;

/**
 * The AgentCore Gateway as an MCP server, through the official MCP Java SDK (streamable HTTP).
 * The Bearer token is this agent's OAuth token from AgentCore Identity, so tools/list is already
 * filtered to what Cedar lets THIS agent use - the investigator never even sees add_ticket_comment.
 */
public final class GatewayTools implements ToolBox {
    private final McpSyncClient client;
    private final List<Spec> specs;

    public GatewayTools(String gatewayUrl, String bearerToken) {
        URI u = URI.create(gatewayUrl);
        String base = u.getScheme() + "://" + u.getAuthority();
        HttpClientStreamableHttpTransport transport = HttpClientStreamableHttpTransport.builder(base)
                .endpoint(u.getPath().isEmpty() ? "/mcp" : u.getPath())
                .httpRequestCustomizer((builder, method, uri, body, ctx) -> builder.header("Authorization", "Bearer " + bearerToken))
                .build();
        this.client = McpClient.sync(transport).requestTimeout(Duration.ofSeconds(60)).build();
        client.initialize();
        List<Spec> all = new ArrayList<>();
        String cursor = null;
        do {
            McpSchema.ListToolsResult page = cursor == null ? client.listTools() : client.listTools(cursor);
            for (McpSchema.Tool t : page.tools()) {
                all.add(new Spec(t.name(), t.description() == null ? t.name() : t.description(), t.inputSchema()));
            }
            cursor = page.nextCursor();
        } while (cursor != null && !cursor.isEmpty());
        this.specs = List.copyOf(all);
    }

    @Override public List<Spec> specs() { return specs; }

    @Override
    public Outcome call(String name, Map<String, Object> input) {
        try {
            McpSchema.CallToolResult r = client.callTool(new McpSchema.CallToolRequest(name, input));
            String text = r.content() == null ? "" : r.content().stream()
                    .map(c -> c instanceof McpSchema.TextContent tc ? tc.text() : String.valueOf(c))
                    .collect(Collectors.joining("\n"));
            return new Outcome(text, Boolean.TRUE.equals(r.isError()));
        } catch (Exception e) {
            // a policy denial arrives as a JSON-RPC error: report it to the model as a failed tool call
            return new Outcome("{\"error\": " + Json.write(String.valueOf(e.getMessage())) + "}", true);
        }
    }

    @Override public void close() { client.close(); }
}
