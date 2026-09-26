package com.airamatrix.agentcore;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;

/**
 * OpenTelemetry GenAI spans, so CloudWatch GenAI Observability and AgentCore EVALUATIONS can read a
 * Java agent. AgentCore Evaluations' "generic framework support" needs:
 *   - a scope name starting with opentelemetry.instrumentation.  (this tracer's name)
 *   - gen_ai.operation.name on every span: invoke_agent | chat | execute_tool
 *   - content in the documented attributes: gen_ai.task.input / gen_ai.task.output (agent),
 *     gen_ai.input.messages / gen_ai.output.messages / gen_ai.system_instructions (model call),
 *     gen_ai.tool.name / gen_ai.tool.call.arguments / gen_ai.tool.call.result (tool call)
 * The ADOT Java agent in the image (-javaagent) exports them; without it (tests) the API is a no-op.
 */
public final class GenAiTelemetry {
    public static final String SCOPE = "opentelemetry.instrumentation.aira_agents";
    private final Tracer tracer = GlobalOpenTelemetry.getTracer(SCOPE, "1.0.0");
    private final String session, role;

    public GenAiTelemetry(String session, String role) {
        this.session = session;
        this.role = role;
    }

    public Span agent(String prompt) {
        return base("invoke_agent " + role, "invoke_agent", SpanKind.INTERNAL)
                .setAttribute("gen_ai.agent.name", role)
                .setAttribute("gen_ai.task.input", prompt);
    }

    public Span chat(String model, String system, String inputMessagesJson) {
        return base("chat " + model, "chat", SpanKind.CLIENT)
                .setAttribute("gen_ai.system", "aws.bedrock")
                .setAttribute("gen_ai.request.model", model)
                .setAttribute("gen_ai.system_instructions", system)
                .setAttribute("gen_ai.input.messages", inputMessagesJson);
    }

    public Span tool(String name, String argumentsJson) {
        return base("execute_tool " + name, "execute_tool", SpanKind.INTERNAL)
                .setAttribute("gen_ai.tool.name", name)
                .setAttribute("gen_ai.tool.call.arguments", argumentsJson);
    }

    private Span base(String name, String op, SpanKind kind) {
        return tracer.spanBuilder(name).setSpanKind(kind).startSpan()
                .setAttribute("gen_ai.operation.name", op)
                .setAttribute("session.id", session)
                .setAttribute("agent.role", role);
    }

    public static Scope activate(Span s) { return s.makeCurrent(); }

    public static void fail(Span s, Throwable e) {
        s.recordException(e);
        s.setStatus(StatusCode.ERROR, String.valueOf(e.getMessage()));
    }
}
