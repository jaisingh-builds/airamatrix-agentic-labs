package com.airamatrix.day4.lab52;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Where things are. The golden set, fixtures and calibration set are NOT copied into the Java
 * module: both harnesses read the Python lab's files, so there is one golden set, one frozen gate.
 */
final class LabPaths {
    private LabPaths() {}

    static final String LAB_REL = "day4-orchestration-evals-cicd/lab5-2-evals";
    static final String MODULE_REL = "day4-orchestration-evals-cicd/java/lab52";
    static final String OPS_REL = "day3-integration-security/aira-ops/aira_ops.py";

    /** The repo root: found from the working directory, or else from where the jar lives. */
    static Path repo() {
        Path r = Spans.repoRoot();
        if (Files.isDirectory(r.resolve(LAB_REL))) return r;
        try {
            Path jar = Paths.get(LabPaths.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            for (Path d = jar; d != null; d = d.getParent()) {
                if (Files.isDirectory(d.resolve(LAB_REL))) return d;
            }
        } catch (Exception ignored) { /* fall through */ }
        return r;
    }

    /** lab5-2-evals/ - golden/, fixtures/, judge_calibration.json. */
    static Path lab() { return repo().resolve(LAB_REL); }

    /** This module - results/ go here. */
    static Path module() { return repo().resolve(MODULE_REL); }

    static Path ops() { return repo().resolve(OPS_REL); }

    /**
     * A path from the command line: as given (relative to the working directory, like Python), or
     * else relative to lab5-2-evals/ or this module, so `--regrade fixtures/live-runs.json` and
     * `--golden golden/holdout.json` work from the repo root.
     */
    static Path resolve(String arg) {
        Path p = Paths.get(arg);
        if (p.isAbsolute() || Files.exists(p)) return p;
        for (Path base : new Path[]{lab(), module()}) {
            if (Files.exists(base.resolve(arg))) return base.resolve(arg);
        }
        return p;
    }

    static JsonNode readJson(Path p) throws IOException {
        return Contracts.JSON.readTree(Files.readString(p, StandardCharsets.UTF_8));
    }

    /** For "results: ..." lines: relative to the working directory when possible. */
    static String show(Path p) {
        try {
            return Paths.get("").toAbsolutePath().relativize(p.toAbsolutePath()).toString();
        } catch (IllegalArgumentException e) {
            return p.toString();
        }
    }
}
