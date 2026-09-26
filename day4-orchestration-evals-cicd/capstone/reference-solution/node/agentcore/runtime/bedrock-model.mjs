// The agent loop's model on Bedrock Converse: the loop speaks the Messages API shape in both modes, and this
// translates. The Bedrock Guardrail is on EVERY call (guardrailConfig) - a prompt change cannot remove it; an
// intervention comes back as stop_reason "guardrail_intervened" and the loop ends the run.
import { GatewayError } from "../../lib/agent.mjs";
import { GenAiTelemetry } from "./telemetry.mjs";

/** Messages API messages -> Converse messages. */
export function toConverse(messages) {
  return messages.map((m) => {
    const role = m.role === "assistant" ? "assistant" : "user";
    const blocks = [];
    if (typeof m.content === "string") blocks.push({ text: m.content });
    else {
      for (const b of m.content || []) {
        if (b.type === "text") { if (String(b.text || "").trim()) blocks.push({ text: b.text }); }
        else if (b.type === "tool_use") blocks.push({ toolUse: { toolUseId: b.id, name: b.name, input: b.input ?? {} } });
        else if (b.type === "tool_result") {
          blocks.push({ toolResult: { toolUseId: b.tool_use_id, content: [{ text: typeof b.content === "string" && b.content ? b.content : "(empty)" }],
            status: b.is_error ? "error" : "success" } });
        }
      }
    }
    if (!blocks.length) blocks.push({ text: "(empty)" });
    return { role, content: blocks };
  });
}

/** Converse response -> a Messages API response: content, stop_reason, usage. */
export function fromConverse(resp) {
  const content = [];
  for (const b of resp.output?.message?.content || []) {
    if (b.text != null) content.push({ type: "text", text: b.text });
    else if (b.toolUse) content.push({ type: "tool_use", id: b.toolUse.toolUseId, name: b.toolUse.name, input: b.toolUse.input ?? {} });
  }
  return { content, stop_reason: resp.stopReason, usage: { input_tokens: resp.usage?.inputTokens ?? 0, output_tokens: resp.usage?.outputTokens ?? 0 } };
}

export class BedrockConverseModel {
  /** converse: async (input) => ConverseCommand output (a BedrockRuntimeClient in production, a fake in tests). */
  constructor(converse, modelId, guardrailId, guardrailVersion, tel) {
    Object.assign(this, { converse, modelId, guardrailId, guardrailVersion, tel });
  }

  async messages(messages, tools, system, maxTokens) {
    const req = {
      modelId: this.modelId,
      system: [{ text: system }],
      messages: toConverse(messages),
      inferenceConfig: { maxTokens },
      guardrailConfig: { guardrailIdentifier: this.guardrailId, guardrailVersion: this.guardrailVersion, trace: "enabled" },
    };
    if (tools?.length) {
      req.toolConfig = { tools: tools.map((t) => ({ toolSpec: { name: t.name, description: t.description, inputSchema: { json: t.input_schema } } })) };
    }
    const chat = this.tel ? this.tel.chat(this.modelId, system, JSON.stringify(messages)) : null;
    try {
      const out = fromConverse(await (chat ? this.tel.within(chat, () => this.converse(req)) : this.converse(req)));
      chat?.setAttributes({ "gen_ai.output.messages": JSON.stringify(out.content), "gen_ai.response.finish_reasons": String(out.stop_reason),
        "gen_ai.usage.input_tokens": out.usage.input_tokens, "gen_ai.usage.output_tokens": out.usage.output_tokens });
      return out;
    } catch (e) {
      if (chat) GenAiTelemetry.fail(chat, e);
      // a Bedrock error (throttling, validation, access) ends the run as a model failure, like a gateway error locally
      throw new GatewayError(e.$metadata?.httpStatusCode ?? 0, `${e.name || "Error"}: ${e.message}`);
    } finally {
      chat?.end();
    }
  }
}
