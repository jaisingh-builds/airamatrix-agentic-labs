package com.airamatrix.capstone;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/** Where things are in the labs checkout. LABS_REPO overrides; otherwise found from the working directory upwards. */
public final class Repo {
    private Repo() {}

    public static Path root() {
        String env = System.getenv("LABS_REPO");
        if (env != null && !env.isBlank()) return Paths.get(env).toAbsolutePath();
        for (Path d = Paths.get("").toAbsolutePath(); d != null; d = d.getParent()) {
            if (Files.isDirectory(d.resolve("labkit")) && Files.isDirectory(d.resolve("day3-integration-security"))) return d;
        }
        throw new IllegalStateException("run from inside the airamatrix-agentic-labs checkout, or set LABS_REPO");
    }

    public static Path opsScript() { return root().resolve("day3-integration-security/aira-ops/aira_ops.py"); }

    /** reference-solution/ - golden/ and fixtures/ are shared by the Java, Python and Node solutions. */
    public static Path solution() { return root().resolve("day4-orchestration-evals-cicd/capstone/reference-solution"); }

    /** reference-solution/java/ - this language's results/ and samples/. */
    public static Path java() { return solution().resolve("java"); }

    /** python3, or python on Windows, unless LAB_PYTHON says otherwise. */
    public static String python() {
        String p = System.getenv("LAB_PYTHON");
        if (p != null && !p.isBlank()) return p;
        return System.getProperty("os.name", "").toLowerCase().contains("win") ? "python" : "python3";
    }
}
