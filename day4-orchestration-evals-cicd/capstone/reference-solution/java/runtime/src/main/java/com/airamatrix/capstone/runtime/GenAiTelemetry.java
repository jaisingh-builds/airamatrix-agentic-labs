package com.airamatrix.capstone.runtime;

import java.util.function.Supplier;

import com.airamatrix.capstone.Telemetry;
import com.airamatrix.capstone.Tools;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;

/**
 * OpenTelemetry GenAI spans for CloudWatch GenAI Observability and AgentCore Evaluations (the 06-agents-java rules:
 * scope name opentelemetry.instrumentation.*, gen_ai.operation.name and session.id on every span). The ADOT Java
 * agent in the image exports them; without it (tests, laptop) the API is a no-op. The JSONL trace is written as well.
 */
public final class GenAiTelemetry implements Telemetry {
    public static final String SCOPE = "opentelemetry.instrumentation.aira_capstone";
    private final Tracer tracer = GlobalOpenTelemetry.getTracer(SCOPE, "1.0.0");
    private final String session;

    public GenAiTelemetry(String session) { this.session = session; }

    public Span agent(String prompt) {
        return base("invoke_agent sla-responder", "invoke_agent", SpanKind.INTERNAL)
                .setAttribute("gen_ai.agent.name", "sla-responder").setAttribute("gen_ai.task.input", prompt);
    }

    public Span chat(String model, String system, String inputMessagesJson) {
        return base("chat " + model, "chat", SpanKind.CLIENT).setAttribute("gen_ai.system", "aws.bedrock")
                .setAttribute("gen_ai.request.model", model).setAttribute("gen_ai.system_instructions", system)
                .setAttribute("gen_ai.input.messages", inputMessagesJson);
    }

    @Override
    public Tools.Result tool(String name, String argumentsJson, Supplier<Tools.Result> call) {
        Span s = base("execute_tool " + name, "execute_tool", SpanKind.INTERNAL)
                .setAttribute("gen_ai.tool.name", name).setAttribute("gen_ai.tool.call.arguments", argumentsJson);
        try (Scope ignored = s.makeCurrent()) {
            Tools.Result r = call.get();
            s.setAttribute("gen_ai.tool.call.result", r.text());
            if (r.error()) s.setAttribute("error.type", "tool_error");
            return r;
        } finally {
            s.end();
        }
    }

    private Span base(String name, String op, SpanKind kind) {
        return tracer.spanBuilder(name).setSpanKind(kind).startSpan()
                .setAttribute("gen_ai.operation.name", op).setAttribute("session.id", session).setAttribute("agent.role", "sla-responder");
    }

    public static void fail(Span s, Throwable e) {
        s.recordException(e);
        s.setStatus(StatusCode.ERROR, String.valueOf(e.getMessage()));
    }
}
