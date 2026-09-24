# vendor/mcp-sdk.mjs — the official MCP TypeScript SDK, as one file

This lab is written against the **official** `@modelcontextprotocol/sdk`. It is
vendored so that nobody runs `npm install` on 22 laptops behind a proxy.

| | |
|---|---|
| Package | `@modelcontextprotocol/sdk` **1.30.1** (+ `zod` 4.6.5, its dependency) |
| Built | 2026-09-25 with esbuild, unminified |
| SHA-256 | `7f8f87fb2b857715af0ceff77d5328bb6de41636bf6332b722d81879f673787c` |
| Exports | `McpServer`, `ResourceTemplate`, `StdioServerTransport`, `z` |

## Why this file is not minified

Day 3 covers supply-chain risk in agent extensions and MCP servers. A vendored
file you cannot read is exactly that risk. This one is readable, pinned, and
hashed — check it before you trust it:

```bash
shasum -a 256 vendor/mcp-sdk.mjs     # must match the SHA-256 above
```

## Rebuild it yourself (needs npm, once, on a networked machine)

```bash
mkdir /tmp/v && cd /tmp/v && npm init -y
npm install @modelcontextprotocol/sdk@1.30.1 esbuild
cat > entry.mjs <<'JS'
export { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
export { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
export { z } from "zod";
JS
npx esbuild entry.mjs --bundle --platform=node --format=esm --target=node22 \
  --outfile=mcp-sdk.mjs --legal-comments=inline
```

If the hash differs from the one above, find out why before you use it.
