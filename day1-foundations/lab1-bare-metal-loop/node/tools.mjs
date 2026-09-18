// The three tools for Lab 1.1, and their schemas.
// The schema IS the prompt: the model only ever sees name, description and
// JSON Schema - never your implementation. Lab 1.2 proves that by breaking them.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WORKSPACE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "workspace");
const ALLOWED_HOSTS = new Set(["127.0.0.1", "localhost"]);

export class ToolError extends Error {}

export function readFile({ path: rel }) {
  const target = path.resolve(WORKSPACE, rel);
  if (!target.startsWith(WORKSPACE)) throw new ToolError(`path escapes the workspace: ${rel}`);
  if (!fs.existsSync(target)) {
    throw new ToolError(`no such file: ${rel}. Available files: ${fs.readdirSync(WORKSPACE).join(", ")}`);
  }
  return fs.readFileSync(target, "utf8");
}

export async function httpGet({ url }) {
  const host = new URL(url).hostname;
  if (!ALLOWED_HOSTS.has(host)) {
    throw new ToolError(`host not allowed: ${host}. Allowed: ${[...ALLOWED_HOSTS].join(", ")}`);
  }
  const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
  return await response.text();
}

export function calculator({ expression }) {
  if (!/^[0-9.+\-*/() ]+$/.test(expression)) {
    throw new ToolError(`expression contains unsupported characters: ${JSON.stringify(expression)}. ` +
      "Only numbers and + - * / ( ) are supported.");
  }
  try {
    // charset-restricted above, so this cannot reach anything else
    const value = Function(`"use strict";return (${expression})`)();
    return String(value);
  } catch (err) {
    throw new ToolError(`could not evaluate ${JSON.stringify(expression)}: ${err.message}`);
  }
}

const REGISTRY = { read_file: readFile, http_get: httpGet, calculator };

export const SCHEMAS = [
  {
    name: "read_file",
    description:
      "Read a UTF-8 text file from the lab workspace directory. Use this to inspect " +
      "configuration and threshold files. Returns the full file contents as text.",
    input_schema: {
      type: "object",
      properties: { path: { type: "string", description: "File name relative to the workspace, e.g. 'limits.txt'." } },
      required: ["path"],
    },
  },
  {
    name: "http_get",
    description:
      "Perform an HTTP GET and return the response body as text. Only the local fixture " +
      "host is reachable: http://127.0.0.1:8137/. Use this to fetch live service status.",
    input_schema: {
      type: "object",
      properties: { url: { type: "string", description: "Absolute URL, e.g. 'http://127.0.0.1:8137/status.json'." } },
      required: ["url"],
    },
  },
  {
    name: "calculator",
    description:
      "Evaluate an arithmetic expression and return the numeric result. Supports + - * / " +
      "and parentheses only. Use this instead of doing arithmetic yourself.",
    input_schema: {
      type: "object",
      properties: { expression: { type: "string", description: "Arithmetic only, e.g. '(812 - 500) / 500'." } },
      required: ["expression"],
    },
  },
];

// Run a tool. Returns [text, ok]. Never throws: the agent must be able to read
// the error and recover.
export async function dispatch(name, args) {
  const fn = REGISTRY[name];
  if (!fn) return [`unknown tool: ${name}. Available: ${Object.keys(REGISTRY).join(", ")}`, false];
  try {
    return [String(await fn(args || {})), true];
  } catch (err) {
    return [`ERROR: ${err.message}`, false];
  }
}
