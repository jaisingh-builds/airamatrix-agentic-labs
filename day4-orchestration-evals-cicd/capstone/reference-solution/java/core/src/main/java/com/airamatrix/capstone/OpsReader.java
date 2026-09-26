package com.airamatrix.capstone;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * The reads the responder needs from aira-ops - and nothing else. Two implementations, one per mode:
 * {@link HttpOpsReader} (local: your own aira-ops, an account-scoped read-only caller token) and, in the
 * runtime module, GatewayOpsReader (AgentCore: the Gateway's read tools over MCP, Cedar-filtered).
 *
 * There is no write method on purpose. The only write in this system is made by {@link Gate#apply},
 * after a human approved it, with a credential no agent process holds.
 */
public interface OpsReader {
    JsonNode account(String accountId);
    /** Tickets of one account, newest first, at most 50: id, title, status, priority. */
    JsonNode tickets(String accountId);
    /** One ticket with its comments. */
    JsonNode ticket(String ticketId);
    /** Slide-analysis jobs of one account. */
    JsonNode jobs(String accountId);
    JsonNode config(String key);

    /** aira-ops said no (404, 401, ...) or could not be reached (status 0). Reported to the model as data. */
    class OpsError extends RuntimeException {
        public final int status;
        public final String code;

        public OpsError(int status, String code, String message) {
            super(message);
            this.status = status;
            this.code = code;
        }
    }
}
