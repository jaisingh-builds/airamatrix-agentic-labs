package com.airamatrix.labkit;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

/** A spend ceiling that refuses the next call, rather than warning after it. */
public final class BudgetGuard {
    public static class BudgetExceeded extends RuntimeException {
        public BudgetExceeded(String message) { super(message); }
    }

    private static final Map<String, double[]> PRICES = Map.of(
        "claude-sonnet", new double[]{0.000002, 0.00001},
        "claude-opus",   new double[]{0.000005, 0.000025},
        "claude-haiku",  new double[]{0.000001, 0.000005});

    private final double limit;
    private final String model;
    private double spent = 0;
    private int calls = 0;

    public BudgetGuard(double limitUsd, String model) { this.limit = limitUsd; this.model = model; }

    /** Call BEFORE each request. */
    public void check() {
        if (spent >= limit) {
            throw new BudgetExceeded(String.format(
                "Budget ceiling hit: $%.4f of $%.2f after %d calls. Raise LAB_BUDGET_USD to continue.",
                spent, limit, calls));
        }
    }

    /** Call AFTER each response. Returns the cost of that call. */
    public double record(JsonNode usage) {
        double[] rate = PRICES.getOrDefault(model, PRICES.get("claude-sonnet"));
        double cost = n(usage, "input_tokens") * rate[0]
                    + n(usage, "cache_read_input_tokens") * rate[0] * 0.1
                    + n(usage, "cache_creation_input_tokens") * rate[0] * 1.25
                    + n(usage, "output_tokens") * rate[1];
        spent += cost; calls++;
        return cost;
    }

    private static double n(JsonNode node, String field) {
        return node == null || node.get(field) == null ? 0 : node.get(field).asDouble();
    }

    public double spent() { return spent; }
    public String summary() {
        return String.format("%d calls, $%.4f of $%.2f", calls, spent, limit);
    }
}
