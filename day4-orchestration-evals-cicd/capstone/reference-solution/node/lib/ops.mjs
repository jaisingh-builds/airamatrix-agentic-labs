// The reads the responder needs from aira-ops - and nothing else. Two implementations, one per mode:
// HttpOpsReader (local: your own aira-ops, an account-scoped read-only caller token) and, in agentcore/,
// GatewayOpsReader (AgentCore: the Gateway's read tools over MCP, Cedar-filtered).
//
// There is no write method on purpose. The only write in this system is made by gate.apply(), after a human
// approved it, with a credential no agent process holds.

/** aira-ops said no (404, 401, ...) or could not be reached (status 0). Reported to the model as data. */
export class OpsError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

export const MAX_BYTES = 256 * 1024;

/**
 * Local mode: aira-ops over HTTP with a READ-ONLY caller token scoped to one account (`capstone tokens`).
 * aira-ops itself enforces the scope: another account's ticket is a 404. Responses are bounded at the source -
 * a body over MAX_BYTES is refused, never sliced (slicing JSON at a byte count produces invalid JSON).
 */
export class HttpOpsReader {
  constructor(baseUrl, token, timeoutMs = 8000) {
    this.baseUrl = baseUrl.replace(/\/+$/, ""); this.token = token; this.timeoutMs = timeoutMs;
  }
  account(id) { return this.get(`/accounts/${enc(id)}`); }
  tickets(accountId) { return this.get(`/tickets?limit=50&account_id=${enc(accountId)}`); }
  ticket(id) { return this.get(`/tickets/${enc(id)}`); }
  jobs(accountId) { return this.get(`/jobs?account_id=${enc(accountId)}`); }
  config(key) { return this.get(`/config/${enc(key)}`); }

  async get(p) {
    let r, body;
    try {
      r = await fetch(this.baseUrl + p, {
        headers: { authorization: `Bearer ${this.token}`, "x-actor": "sla-responder" },
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      body = await readBounded(r, MAX_BYTES);
    } catch (e) {
      if (e instanceof OpsError) throw e;
      throw new OpsError(0, "unavailable", `aira-ops unreachable: ${e.name === "TimeoutError" ? "HttpTimeoutException" : "ConnectException"}`);
    }
    let json;
    try { json = body.length ? JSON.parse(body) : {}; } catch { throw new OpsError(502, "invalid", "aira-ops returned invalid JSON"); }
    if (r.status >= 400) {
      const e = json.error || {};
      throw new OpsError(r.status, e.code || `http_${r.status}`, e.message || `HTTP ${r.status}`);
    }
    return json;
  }
}

async function readBounded(r, max) {
  const chunks = [];
  let n = 0;
  for await (const c of r.body || []) {
    n += c.length;
    if (n > max) {
      try { await r.body.cancel(); } catch { /* already closed */ }
      throw new OpsError(502, "too_large", `aira-ops response over ${max} bytes - refused, not truncated`);
    }
    chunks.push(c);
  }
  return Buffer.concat(chunks.map((c) => Buffer.from(c))).toString("utf8");
}

const enc = (s) => encodeURIComponent(s);
