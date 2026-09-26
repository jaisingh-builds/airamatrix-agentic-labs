// Shared test helpers: a scripted model, a fake write side, the reference "good" proposal.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ResponderAgent } from "../lib/agent.mjs";
import { hex } from "../lib/private-ops.mjs";
import { Tracer } from "../lib/spans.mjs";
import { parseInstant } from "../lib/time.mjs";

export const T1030 = parseInstant("2026-09-24T10:30:00+05:30");
export const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "capstone-node-test-"));
process.env.LAB_TRACE_DIR = path.join(TMP, "traces");      // tests never write into the repo's traces/

export const tracer = () => new Tracer("capstone", `test-${hex(3)}`);

export const okComment = () => "We know last night's slides are still queued and your reports are delayed. Our team is working to clear "
  + "the backlog now and we will update this ticket within the hour.";

export function good() {
  return {
    summary: "J-5501 has breached its turnaround and T-1001 is ten minutes from breaching; the ingest backlog is the cause.",
    exposed: [
      { item: "J-5501", state: "breached", elapsed_minutes: 275, target_minutes: 240 },
      { item: "T-1001", state: "at_risk", elapsed_minutes: 230, target_minutes: 240 },
    ],
    likely_cause: "Worker slots were cut on 23 Sep (config ingest.max_concurrent_jobs 16 -> 4).",
    evidence: ["T-1001 on-call comment: queue depth 212", "sla_report: J-5501 275/240 min"],
    untrusted_instructions_seen: [],
    action: { type: "post_customer_update", ticket_id: "T-1001", comment: okComment(), reason: "T-1001 is at risk and the customer is waiting" },
  };
}

export function withAction(tid, comment) {
  const p = good();
  p.action.ticket_id = tid;
  p.action.comment = comment;
  return p;
}

export function toolUse(name, input) {
  return { stop_reason: "tool_use", content: [{ type: "tool_use", id: `tu_${hex(4)}`, name, input }], usage: { input_tokens: 1000, output_tokens: 100 } };
}

export function text(t) {
  return { stop_reason: "end_turn", content: [{ type: "text", text: t }], usage: { input_tokens: 500, output_tokens: 20 } };
}

/** The model, as a script: one response per call. */
export class Script {
  constructor(...replies) { this.replies = replies; this.calls = 0; this.fallback = null; this.seen = []; }
  async messages(messages) {
    this.seen.push(JSON.parse(JSON.stringify(messages)));
    const i = this.calls++;
    if (i < this.replies.length) return this.replies[i];
    if (this.fallback) return JSON.parse(JSON.stringify(this.fallback));
    throw new Error(`the script ran out at call ${i + 1}`);
  }
}

/** A write side with a controllable outcome. */
export class Fake {
  constructor(status) { this.status = status; this.ticketStatus = "open"; this.posts = 0; this.keys = []; }
  async getTicket() { return { status: 200, body: { status: this.ticketStatus } }; }
  async postComment(id, comment, key) { this.posts++; this.keys.push(key); return { status: this.status, body: {} }; }
}

export const agent = (m, budget, turns) => new ResponderAgent(m, "claude-sonnet", turns, budget, () => undefined);

export const spanNames = (file) => fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l).name);
