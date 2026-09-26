// OpenTelemetry GenAI spans for CloudWatch GenAI Observability and AgentCore Evaluations (the 06-agents-java
// rules: scope name opentelemetry.instrumentation.*, gen_ai.operation.name and session.id on every span). The ADOT
// Node distro loaded by app.js exports them; without it (tests, laptop) the API is a no-op. The JSONL trace is
// written as well - it is the one the CLI shows.
import { SpanKind, SpanStatusCode, context, trace } from "@opentelemetry/api";

export const SCOPE = "opentelemetry.instrumentation.aira_capstone";

export class GenAiTelemetry {
  constructor(session) { this.session = session; this.tracer = trace.getTracer(SCOPE, "1.0.0"); }

  base(name, op, kind) {
    return this.tracer.startSpan(name, { kind, attributes: { "gen_ai.operation.name": op, "session.id": this.session, "agent.role": "sla-responder" } });
  }

  agent(prompt) {
    const s = this.base("invoke_agent sla-responder", "invoke_agent", SpanKind.INTERNAL);
    s.setAttributes({ "gen_ai.agent.name": "sla-responder", "gen_ai.task.input": prompt });
    return s;
  }

  chat(model, system, inputMessagesJson) {
    const s = this.base(`chat ${model}`, "chat", SpanKind.CLIENT);
    s.setAttributes({ "gen_ai.system": "aws.bedrock", "gen_ai.request.model": model, "gen_ai.system_instructions": system,
      "gen_ai.input.messages": inputMessagesJson });
    return s;
  }

  /** Run fn with span as the active span (so child spans nest under it). */
  within(span, fn) { return context.with(trace.setSpan(context.active(), span), fn); }

  /** The agent loop's hook around every tool call. */
  async tool(name, argumentsJson, call) {
    const s = this.base(`execute_tool ${name}`, "execute_tool", SpanKind.INTERNAL);
    s.setAttributes({ "gen_ai.tool.name": name, "gen_ai.tool.call.arguments": argumentsJson });
    try {
      const r = await this.within(s, call);
      s.setAttribute("gen_ai.tool.call.result", r.text);
      if (r.error) s.setAttribute("error.type", "tool_error");
      return r;
    } finally {
      s.end();
    }
  }

  static fail(span, e) {
    span.recordException(e);
    span.setStatus({ code: SpanStatusCode.ERROR, message: String(e?.message ?? e) });
  }
}
