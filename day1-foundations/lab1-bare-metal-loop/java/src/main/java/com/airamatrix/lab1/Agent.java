package com.airamatrix.lab1;

import com.airamatrix.labkit.*;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.*;

/**
 * Lab 1.1 - Build an agent loop from scratch. No framework.
 *
 * You are writing the loop. That is the whole exercise: everything in the rest
 * of the programme sits on top of the cycle you are about to implement.
 *
 *     observe -> decide -> act -> observe
 *
 * Run it:   mvn -q compile exec:java -pl day1-foundations/lab1-bare-metal-loop/java -am
 * Check it: mvn -q test -pl day1-foundations/lab1-bare-metal-loop/java -am
 *
 * This is the worked solution (branch `solutions`). The starter, with the five
 * TODOs, is on `main`. Every stop_reason has its own branch: see runAgent below.
 */
public final class Agent {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    static final String SYSTEM =
        "You are an operations assistant. Use the provided tools to gather facts "
      + "before answering. Never guess a number you could compute with the calculator, "
      + "and never invent file contents. When you have the answer, state it plainly.";

    /** stop_reason "max_tokens": the reply was cut off, so it is not an answer. */
    public static class Truncated extends RuntimeException {
        public Truncated(String message) { super(message); }
    }

    /** stop_reason "refusal": the model declined to continue. */
    public static class Refused extends RuntimeException {
        public Refused(String message) { super(message); }
    }

    /** A stop_reason this code has never seen. Fail safely and keep the trace. */
    public static class UnhandledStop extends RuntimeException {
        public UnhandledStop(String stopReason) { super("unhandled stop_reason: " + stopReason); }
    }

    public static class StepLimitExceeded extends RuntimeException {
        public StepLimitExceeded(String message) { super(message); }
    }

    public static String runAgent(String goal, Integer maxSteps, boolean verbose) {
        Config cfg = new Config();
        GatewayClient client = new GatewayClient(cfg);
        Tracer tracer = new Tracer("lab1");
        BudgetGuard budget = new BudgetGuard(cfg.budgetUsd, cfg.model);
        int limit = maxSteps != null ? maxSteps : cfg.maxSteps;

        List<Map<String, Object>> messages = new ArrayList<>();
        messages.add(Map.of("role", "user", "content", goal));
        tracer.emit("start", Map.of("goal", goal, "model", cfg.model, "max_steps", limit));

        for (int step = 1; step <= limit; step++) {
            budget.check();

            JsonNode response = client.messages(messages, Tools.schemas(), SYSTEM, 1024);

            String stop = response.path("stop_reason").asText(null);
            JsonNode blocks = response.path("content");
            List<JsonNode> calls = new ArrayList<>();
            StringBuilder textBuilder = new StringBuilder();
            for (JsonNode block : blocks) {
                if ("tool_use".equals(block.path("type").asText())) {
                    calls.add(block);
                } else if ("text".equals(block.path("type").asText())) {
                    textBuilder.append(block.path("text").asText());
                }
            }
            String text = textBuilder.toString().strip();
            List<String> toolNames = calls.stream().map(c -> c.path("name").asText()).toList();
            double cost = budget.record(response.get("usage"));
            tracer.step(step, stop == null ? "none" : stop, text, toolNames);
            if (verbose) {
                System.out.printf("  step %d: stop=%s tools=%s ($%.4f, %s)%n",
                    step, stop, toolNames.isEmpty() ? "-" : String.join(",", toolNames),
                    cost, budget.summary());
            }

            // One branch per stop_reason. "Not tool_use" is not a synonym for
            // "done": that is how a truncated or refused reply gets reported as
            // a finished answer.
            if ("end_turn".equals(stop) || "stop_sequence".equals(stop)) {
                if (text.isBlank()) {
                    throw new UnhandledStop("model ended the turn with no text (stop_reason=" + stop + ")");
                }
                tracer.emit("finish", Map.of("answer", text.length() > 400 ? text.substring(0, 400) : text,
                                             "spend", budget.summary()));
                return text;
            }
            if ("max_tokens".equals(stop)) {
                tracer.emit("stopped", Map.of("reason", "max_tokens", "step", step));
                throw new Truncated("reply was cut off at the token limit after " + step
                    + " steps. " + budget.summary());
            }
            if ("refusal".equals(stop)) {
                tracer.emit("stopped", Map.of("reason", "refusal", "step", step));
                throw new Refused("the model declined to continue. " + budget.summary());
            }
            if ("pause_turn".equals(stop)) {
                messages.add(Map.of("role", "assistant", "content", MAPPER.convertValue(blocks, List.class)));
                continue;                                   // resume a long-running turn
            }
            if (!"tool_use".equals(stop)) {
                tracer.emit("stopped", Map.of("reason", "unhandled stop_reason=" + stop, "step", step));
                throw new UnhandledStop(String.valueOf(stop));
            }

            // The assistant turn goes in BEFORE the results, and every tool_use
            // block gets a matching tool_result in ONE user message.
            messages.add(Map.of("role", "assistant", "content", MAPPER.convertValue(blocks, List.class)));
            List<Map<String, Object>> results = new ArrayList<>();
            for (JsonNode call : calls) {
                String name = call.path("name").asText();
                @SuppressWarnings("unchecked")
                Map<String, Object> args = MAPPER.convertValue(call.path("input"), Map.class);
                Object[] outcome = Tools.dispatch(name, args == null ? Map.of() : args);
                String out = String.valueOf(outcome[0]);
                boolean ok = (Boolean) outcome[1];
                tracer.tool(name, args == null ? Map.of() : args, out, ok);
                results.add(Map.of("type", "tool_result",
                                   "tool_use_id", call.path("id").asText(),
                                   "content", out,
                                   "is_error", !ok));
            }
            messages.add(Map.of("role", "user", "content", results));
        }

        // Falling out of the loop means the agent never finished. That is the step
        // limit doing its job - an agent without one is a production incident.
        tracer.emit("step_limit", Map.of("limit", limit));
        throw new StepLimitExceeded("agent did not finish within " + limit
            + " steps. " + budget.summary());
    }

    public static void main(String[] args) {
        FixtureServer.serveInBackground();
        String goal = args.length > 0 ? String.join(" ", args)
            : "Fetch the ingest-tier status from http://127.0.0.1:8137/status.json, "
            + "read limits.txt from the workspace, and tell me whether the service is "
            + "over capacity. If it is, compute by what percentage the queue depth "
            + "exceeds the limit, and name the escalation contact.";
        System.out.println("GOAL: " + goal + "\n");
        try {
            System.out.println("\nANSWER:\n" + runAgent(goal, null, true));
        } catch (StepLimitExceeded | BudgetGuard.BudgetExceeded
                 | Truncated | Refused | UnhandledStop e) {
            System.out.println("\nHALTED: " + e.getMessage());
            System.exit(1);
        }
    }
}
