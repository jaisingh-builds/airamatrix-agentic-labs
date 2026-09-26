// Tracing spans - one JSON line per span, the SAME record format as day4 common/spans.py, so
// `python3 day4-orchestration-evals-cicd/common/trace_view.py <file>` prints a Node run too.
//
//   const tr = new Tracer("capstone", runId);
//   await tr.within("tool", { tool: "get_ticket", input }, async (s) => { ...; s.set("ok", true); });
//
// Same rules as the Python sink, in the same order: only ALLOWED attribute names are written (anything else
// becomes "[dropped]"); tool input keeps identifiers and replaces free text with its length; every value is
// REDACTED at write time (bearer tokens, sk- keys, 32+ hex, the value of any secret-named environment
// variable); long strings are cut to 600 chars. An exception inside within() marks the span failed.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { traceDir } from "./repo.mjs";

export const MAX_ATTR = 600;
export const SECRET_NAMES = /(token|secret|passw(or)?d|credential|api_?key|private_?key|auth|_key$)/i;
const PATTERNS = [
  [/bearer\s+[A-Za-z0-9._\-]{8,}/gi, "Bearer [REDACTED]"],
  [/sk-[A-Za-z0-9_\-]{8,}/g, "sk-[REDACTED]"],
  [/\b[0-9a-f]{32,}\b/g, "[REDACTED-HEX]"],
];
export const ALLOWED_ATTRS = new Set(["account", "action", "approver", "attempt", "base", "case", "cost_usd", "decision",
  "denials", "diff_bytes", "dropped", "exit_code", "files", "head", "http_status", "input", "kept", "ok", "op_id",
  "override", "reason", "replayed", "stage", "tool", "tool_calls", "turns", "verdict"]);
const ID_LIKE = /^[A-Za-z0-9._:/-]{1,64}$/;

const hex12 = () => crypto.randomUUID().replace(/-/g, "").slice(0, 12);

export function minimise(attrs, allowed = ALLOWED_ATTRS) {
  const out = {};
  for (const [k, v] of Object.entries(attrs)) {
    if (!allowed.has(k)) out[k] = "[dropped]";
    else if (k === "input" && v && typeof v === "object" && !Array.isArray(v)) {
      out[k] = Object.fromEntries(Object.entries(v).map(([ik, iv]) =>
        [ik, typeof iv === "string" && !ID_LIKE.test(iv) ? `[text: ${iv.length} chars]` : iv]));
    } else out[k] = v;
  }
  return out;
}

function secretValues() {
  return Object.entries(process.env).filter(([k, v]) => SECRET_NAMES.test(k) && v && v.length >= 8).map(([, v]) => v);
}

/** Mask secrets in any JSON-able value. limit 0 keeps the full length (text that is sent on). */
export function redact(value, limit = MAX_ATTR) {
  if (Array.isArray(value)) return value.map((v) => redact(v, limit));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, SECRET_NAMES.test(k) ? "[REDACTED]" : redact(v, limit)]));
  }
  if (typeof value !== "string") return value;
  let s = value;
  for (const secret of secretValues()) s = s.split(secret).join("[REDACTED]");
  for (const [re, rep] of PATTERNS) s = s.replace(re, rep);
  if (limit && s.length > limit) s = s.slice(0, limit) + `...[+${s.length - limit} chars]`;
  return s;
}

export class Span {
  constructor(tracer, name, parentId, attrs) {
    this.tracer = tracer; this.name = name; this.parentId = parentId;
    this.spanId = hex12();
    this.attrs = { ...attrs }; this.status = "ok"; this.error = null;
    this.start = Date.now() / 1000;
  }
  set(k, v) { this.attrs[k] = v; return this; }
  /** Redact FIRST, then cut: truncation is not redaction. */
  fail(err) { this.status = "error"; this.error = redact(String(err instanceof Error ? err.message : err), 300); return this; }
}

export class Tracer {
  constructor(name, traceId, dir) {
    this.traceId = traceId || hex12();
    const root = dir || traceDir();
    fs.mkdirSync(root, { recursive: true });
    this.path = path.join(root, `${name}-${this.traceId}.jsonl`);
    this.stack = [];
    this.records = [];            // AgentCore mode returns the trace with the result
  }

  begin(name, attrs = {}) {
    const s = new Span(this, name, this.stack.length ? this.stack[this.stack.length - 1].spanId : null, attrs);
    this.stack.push(s);
    return s;
  }

  end(s) {
    const i = this.stack.lastIndexOf(s);
    if (i >= 0) this.stack.splice(i, 1);
    const rec = {
      trace_id: this.traceId, span_id: s.spanId, parent_id: s.parentId, name: s.name,
      start: Math.round(s.start * 1000) / 1000, duration_ms: Math.round(Date.now() - s.start * 1000),
      status: s.status, error: s.error, attrs: redact(minimise(s.attrs), MAX_ATTR),
    };
    this.records.push(rec);
    try { fs.appendFileSync(this.path, JSON.stringify(rec) + "\n"); } catch (e) { process.stderr.write(`trace write failed: ${e.message}\n`); }
  }

  /** Run fn(span) inside a span; an exception marks it failed (if nothing else did) and is re-thrown. */
  async within(name, attrs, fn) {
    const s = this.begin(name, attrs);
    try { return await fn(s); } catch (e) { if (s.status === "ok") s.fail(`${e.constructor?.name || "Error"}: ${e.message}`); throw e; } finally { this.end(s); }
  }

  withinSync(name, attrs, fn) {
    const s = this.begin(name, attrs);
    try { return fn(s); } catch (e) { if (s.status === "ok") s.fail(`${e.constructor?.name || "Error"}: ${e.message}`); throw e; } finally { this.end(s); }
  }

  /** A zero-duration span: a decision, an approval. */
  event(name, attrs = {}) { this.end(this.begin(name, attrs)); }
}

/** common/trace_view.py: print a JSONL trace as a tree (x = failed span). */
export function renderTrace(file, out = (l) => console.log(l)) {
  const spans = fs.readFileSync(file, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
  const kids = new Map();
  for (const s of spans) {
    const k = s.parent_id ?? "";
    if (!kids.has(k)) kids.set(k, []);
    kids.get(k).push(s);
  }
  for (const v of kids.values()) v.sort((a, b) => a.start - b.start);
  const walk = (parent, depth) => {
    for (const s of kids.get(parent) || []) {
      const attrs = Object.entries(s.attrs || {}).filter(([k]) => k !== "output")
        .map(([k, v]) => ` ${k}=${JSON.stringify(v).slice(0, 70)}`).join("");
      const pad = "  ".repeat(depth);
      out(`${pad}${s.status === "error" ? "x" : "-"} ${s.name}  ${s.duration_ms} ms${attrs}`);
      if (s.error) out(`${pad}    ERROR ${s.error}`);
      walk(s.span_id, depth + 1);
    }
  };
  walk("", 0);
}
