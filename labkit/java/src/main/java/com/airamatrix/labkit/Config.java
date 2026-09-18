package com.airamatrix.labkit;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/** Where to call, as what, and with what ceiling. */
public final class Config {
    public final String baseUrl;
    public final String apiKey;
    public final String model;
    public final int maxSteps;
    public final double budgetUsd;

    public Config() {
        loadDotenv();
        this.baseUrl = env("ANTHROPIC_BASE_URL", "").replaceAll("/$", "");
        String key = env("ANTHROPIC_AUTH_TOKEN", "");
        this.apiKey = key.isEmpty() ? env("ANTHROPIC_API_KEY", "") : key;
        this.model = env("LAB_MODEL", "claude-sonnet");
        this.maxSteps = Integer.parseInt(env("LAB_MAX_STEPS", "8"));
        this.budgetUsd = Double.parseDouble(env("LAB_BUDGET_USD", "0.50"));
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) value = System.getProperty(name);
        return (value == null || value.isEmpty()) ? fallback : value;
    }

    /** Load the repo-root .env into system properties, without a dependency. */
    private static void loadDotenv() {
        Path dir = Paths.get("").toAbsolutePath();
        for (int i = 0; i < 8 && dir != null; i++, dir = dir.getParent()) {
            Path candidate = dir.resolve(".env");
            if (!Files.exists(candidate)) continue;
            try {
                for (String line : Files.readAllLines(candidate)) {
                    line = line.trim();
                    if (line.isEmpty() || line.startsWith("#") || !line.contains("=")) continue;
                    int idx = line.indexOf('=');
                    String k = line.substring(0, idx).trim();
                    String v = line.substring(idx + 1).trim().replaceAll("^[\"']|[\"']$", "");
                    if (System.getenv(k) == null && System.getProperty(k) == null) {
                        System.setProperty(k, v);
                    }
                }
            } catch (IOException ignored) { /* fall through to env vars */ }
            return;
        }
    }

    public Config require() {
        StringBuilder missing = new StringBuilder();
        if (baseUrl.isEmpty()) missing.append("ANTHROPIC_BASE_URL ");
        if (apiKey.isEmpty()) missing.append("ANTHROPIC_AUTH_TOKEN ");
        if (missing.length() > 0) {
            throw new IllegalStateException("Missing: " + missing.toString().trim()
                + "\nCopy .env.example to .env and paste the gateway URL and your key."
                + "\nSee setup/05-verify.md.");
        }
        return this;
    }
}
