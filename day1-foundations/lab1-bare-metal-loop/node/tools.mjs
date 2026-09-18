// The three tools for Lab 1.1, and their schemas.
// The schema IS the prompt: the model only ever sees name, description and
// JSON Schema - never your implementation. Lab 1.2 proves that by breaking them.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WORKSPACE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "workspace");
const ALLOWED_HOSTS = new Set(["127.0.0.1", "localhost"]);

export class ToolError extends Error {}

const MAX_BYTES = 20_000;      // bound at the source, not after the read

export function readFile({ path: rel }) {
  const target = path.resolve(WORKSPACE, rel);
  // relative(), not startsWith(): "/workspace-evil" starts with "/workspace".
  const rel2 = path.relative(WORKSPACE, target);
  if (rel2.startsWith("..") || path.isAbsolute(rel2)) {
    throw new ToolError(`path escapes the workspace: ${rel}`);
  }
  if (!fs.existsSync(target)) {
    throw new ToolError(`no such file: ${rel}. Available files: ${fs.readdirSync(WORKSPACE).join(", ")}`);
  }
  return fs.readFileSync(target, "utf8").slice(0, MAX_BYTES);
}

export async function httpGet({ url }) {
  checkHost(url);
  // manual redirects: a redirect is a second request to a host you never checked
  let current = url;
  for (let hop = 0; hop < 3; hop++) {
    const response = await fetch(current, { redirect: "manual", signal: AbortSignal.timeout(10_000) });
    const location = response.headers.get("location");
    if (response.status >= 300 && response.status < 400 && location) {
      current = new URL(location, current).toString();
      checkHost(current);
      continue;
    }
    const body = await response.text();
    return body.slice(0, MAX_BYTES);
  }
  throw new ToolError("too many redirects");
}

function checkHost(url) {
  const host = new URL(url).hostname;
  if (!ALLOWED_HOSTS.has(host)) {
    throw new ToolError(`host not allowed: ${host}. Allowed: ${[...ALLOWED_HOSTS].join(", ")}`);
  }
}

// A small shunting-yard evaluator. Not Function(), not eval(): an agent's
// calculator is a classic path to code execution, and a character filter is a
// denylist you have to keep right forever.
export function calculator({ expression }) {
  if (!/^[0-9.+\-*/%() ]+$/.test(expression)) {
    throw new ToolError(`expression contains unsupported characters: ${JSON.stringify(expression)}. ` +
      "Only numbers and + - * / % ( ) are supported.");
  }
  const tokens = expression.match(/\d+\.?\d*|[-+*/%()]/g);
  if (!tokens) throw new ToolError("nothing to evaluate");
  const prec = { "+": 1, "-": 1, "*": 2, "/": 2, "%": 2 };
  const out = [], ops = [];
  let prev = null;
  for (const t of tokens) {
    if (/^\d/.test(t)) { out.push(Number(t)); }
    else if (t === "(") { ops.push(t); }
    else if (t === ")") {
      while (ops.length && ops.at(-1) !== "(") out.push(ops.pop());
      if (!ops.length) throw new ToolError("unbalanced brackets");
      ops.pop();
    } else {
      if (t === "-" && (prev === null || prev === "(" || prec[prev])) out.push(0);   // unary minus
      while (ops.length && prec[ops.at(-1)] >= prec[t]) out.push(ops.pop());
      ops.push(t);
    }
    prev = t;
  }
  while (ops.length) {
    const op = ops.pop();
    if (op === "(") throw new ToolError("unbalanced brackets");
    out.push(op);
  }
  const stack = [];
  for (const t of out) {
    if (typeof t === "number") { stack.push(t); continue; }
    const b = stack.pop(), a = stack.pop();
    if (a === undefined || b === undefined) throw new ToolError(`malformed expression: ${expression}`);
    if ((t === "/" || t === "%") && b === 0) throw new ToolError("division by zero");
    stack.push(t === "+" ? a + b : t === "-" ? a - b : t === "*" ? a * b : t === "/" ? a / b : a % b);
  }
  if (stack.length !== 1) throw new ToolError(`malformed expression: ${expression}`);
  return String(stack[0]);
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
