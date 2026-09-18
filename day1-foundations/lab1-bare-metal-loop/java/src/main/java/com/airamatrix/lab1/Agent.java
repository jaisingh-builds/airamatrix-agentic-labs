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
 * Five TODOs. Work top to bottom. The reference solution is on the `solutions`
 * branch - try each TODO before you look.
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

            // ---------------------------------------------------------- TODO 1
            // Call the model. Pass the conversation so far, Tools.schemas() and
            // SYSTEM. See GatewayClient.messages(...) for the signature.
            //
            //   JsonNode response = client.messages(messages, Tools.schemas(), SYSTEM, 1024);
            throw new UnsupportedOperationException("TODO 1: call the model");

            // ---------------------------------------------------------- TODO 2
            // Pull out what you need:
            //   stop   - response.path("stop_reason").asText()
            //   blocks - response.path("content")
            //   calls  - blocks whose "type" is "tool_use"
            //   text   - concatenated "text" of blocks whose "type" is "text"
            // Record the cost: budget.record(response.get("usage"))

            // ---------------------------------------------------------- TODO 3
            // Branch on stop. There is NO single "not tool_use means done"
            // branch - that is how an agent reports a truncated or refused reply
            // as a finished answer:
            //
            //   "tool_use"                    -> fall through to TODO 4
            //   "end_turn" / "stop_sequence"  -> trace it and return the text,
            //                                    after checking it is not blank
            //   "max_tokens"                  -> throw new Truncated(...)
            //   "refusal"                     -> throw new Refused(...)
            //   "pause_turn"                  -> resend the conversation
            //                                    unchanged to continue
            //   anything else                 -> throw new UnhandledStop(stop)

            // ---------------------------------------------------------- TODO 4
            // Otherwise the model wants tools. Two rules that are easy to get wrong:
            //   a) add the assistant's blocks to messages BEFORE the results
            //   b) EVERY tool_use block needs a matching tool_result, and they all
            //      go back in ONE user message. Splitting them quietly teaches the
            //      model to stop calling tools in parallel.
            //
            //   Object[] outcome = Tools.dispatch(name, args);
            //   Map.of("type","tool_result","tool_use_id",id,"content",out,"is_error",!ok)

            // ---------------------------------------------------------- TODO 5
            // Add the results as a single {"role":"user"} message and loop again.
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
        } catch (StepLimitExceeded | BudgetGuard.BudgetExceeded e) {
            System.out.println("\nHALTED: " + e.getMessage());
            System.exit(1);
        }
    }
}
