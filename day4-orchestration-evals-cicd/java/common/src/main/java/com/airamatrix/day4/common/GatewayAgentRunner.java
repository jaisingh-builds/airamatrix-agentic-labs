package com.airamatrix.day4.common;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import com.airamatrix.labkit.BudgetGuard;
import com.airamatrix.labkit.Config;
import com.airamatrix.labkit.GatewayClient;
import com.airamatrix.labkit.GatewayError;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * The Java equivalent of the Python SdkRunner: runs one stage as a tool-use loop against the
 * gateway's Messages API. What the SDK options did, this class does in plain code:
 *
 * <ul>
 *   <li>tools: the four read-only aira-ops tools and nothing else (no Bash, no files, no writes)</li>
 *   <li>structured output: a final {@code submit_result} tool whose input_schema IS the contract;
 *       the answer is validated, and a contract error is sent back so the model can fix it</li>
 *   <li>max_turns and max_budget_usd: enforced before every call; a failure reports what it cost</li>
 *   <li>least privilege per process: refuses to start while a write/admin token is in this
 *       process's environment - agents and apply run as separate commands</li>
 * </ul>
 */
public class GatewayAgentRunner implements AgentRunner {
    public static final List<String> FORBIDDEN_ENV = List.of("AIRA_OPS_APPLY_TOKEN", "AIRA_OPS_TOKEN");
    static final String SUBMIT = "submit_result";
    static final int MAX_TOKENS = 4000;
    static final int CONTRACT_RETRIES = 2;

    private final ModelClient model;
    private final String pricingModel;
    private final String opsUrl, readToken;
    private final int maxTurns;
    private final double maxBudgetUsd;
    private final Function<String, String> env;

    public GatewayAgentRunner(ModelClient model, String pricingModel, String opsUrl, String readToken,
                              int maxTurns, double maxBudgetUsd, Function<String, String> env) {
        this.model = model;
        this.pricingModel = pricingModel;
        this.opsUrl = opsUrl;
        this.readToken = readToken;
        this.maxTurns = maxTurns;
        this.maxBudgetUsd = maxBudgetUsd;
        this.env = env;
    }

    /** The real thing: gateway URL, key and model from .env / the environment (labkit Config). */
    public static GatewayAgentRunner fromEnv(String opsUrl, String readToken, double maxBudgetUsd) {
        Config cfg = new Config();
        GatewayClient gw = new GatewayClient(cfg);
        return new GatewayAgentRunner(gw::messages, cfg.model, opsUrl, readToken, 14, maxBudgetUsd, System::getenv);
    }

    @Override
    public AgentResult run(String stage, String system, String prompt, JsonNode schema) {
        List<String> held = FORBIDDEN_ENV.stream().filter(k -> {
            String v = env.apply(k);
            return v != null && !v.isEmpty();
        }).toList();
        if (!held.isEmpty()) {   // fail closed
            throw new RunnerException("refusing to start the " + stage + " agent: " + String.join(", ", held)
                    + " is set in this process. A process that runs agents holds no write or admin token - "
                    + "run agents and apply as separate commands (lab51 run / apply).");
        }

        AiraOpsTools tools = new AiraOpsTools(opsUrl, readToken, "pipeline:" + stage);
        ArrayNode toolDefs = AiraOpsTools.definitions();
        toolDefs.addObject().put("name", SUBMIT)
                .put("description", "Call this exactly once, with your final answer, when you are done. "
                        + "The input must match the schema exactly - it is validated.")
                .set("input_schema", schema);
        List<JsonNode> toolList = new ArrayList<>();
        toolDefs.forEach(toolList::add);

        List<Object> messages = new ArrayList<>();
        messages.add(Map.of("role", "user", "content", prompt));
        BudgetGuard budget = new BudgetGuard(maxBudgetUsd, pricingModel);
        List<ToolCall> calls = new ArrayList<>();
        List<Boolean> ok = new ArrayList<>();
        int contractErrors = 0, nudges = 0;
        String sys = system + "\n\nWhen you have your answer, call the " + SUBMIT + " tool with it. "
                + "Do not write the answer as text.";

        for (int turn = 1; turn <= maxTurns; turn++) {
            JsonNode resp;
            try {
                budget.check();
                resp = model.messages(messages, toolList, sys, MAX_TOKENS);
            } catch (BudgetGuard.BudgetExceeded e) {
                throw new RunnerException(stage + ": error_max_budget_usd after " + (turn - 1) + " turns: " + e.getMessage(),
                        budget.spent(), turn - 1);
            } catch (GatewayError e) {
                throw new RunnerException(stage + ": gateway error " + e.status + ": " + Spans.redact(String.valueOf(e.getMessage())),
                        budget.spent(), turn - 1);
            }
            budget.record(resp.get("usage"));
            JsonNode content = resp.path("content");
            messages.add(Map.of("role", "assistant", "content", content));

            ArrayNode results = Contracts.JSON.createArrayNode();
            JsonNode submitted = null;
            for (JsonNode block : content) {
                if (!"tool_use".equals(block.path("type").asText())) continue;
                String name = block.path("name").asText();
                JsonNode input = block.path("input");
                ObjectNode r = results.addObject().put("type", "tool_result").put("tool_use_id", block.path("id").asText());
                if (name.equals(SUBMIT)) {
                    try {
                        Contracts.validate(input, schema);
                        submitted = input;
                        r.put("content", "accepted");
                    } catch (Contracts.ContractError e) {
                        contractErrors++;
                        r.put("is_error", true).put("content", "contract error: " + e.getMessage() + " - fix it and call "
                                + SUBMIT + " again.");
                    }
                } else {
                    AiraOpsTools.Result res = tools.call(name, input);
                    calls.add(new ToolCall(name, input));
                    ok.add(!res.isError());
                    r.put("content", res.text());
                    if (res.isError()) r.put("is_error", true);
                }
            }
            if (submitted != null) {
                return new AgentResult(submitted, budget.spent(), calls, turn, ok);
            }
            if (contractErrors > CONTRACT_RETRIES) {
                throw new RunnerException(stage + ": the agent could not produce output matching the contract after "
                        + contractErrors + " attempts", budget.spent(), turn);
            }
            if (!results.isEmpty()) {
                messages.add(Map.of("role", "user", "content", results));
                continue;
            }
            // No tool call at all: the model answered in text. Ask once for the structured answer.
            if (++nudges > 1) {
                throw new RunnerException(stage + ": the agent produced no result (it answered in text, not with "
                        + SUBMIT + ")", budget.spent(), turn);
            }
            messages.add(Map.of("role", "user", "content", "Call " + SUBMIT + " now with your final answer."));
        }
        throw new RunnerException(stage + ": error_max_turns after " + maxTurns + " turns", budget.spent(), maxTurns);
    }
}
