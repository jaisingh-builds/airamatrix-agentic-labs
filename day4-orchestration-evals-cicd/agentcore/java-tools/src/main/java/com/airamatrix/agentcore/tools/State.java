package com.airamatrix.agentcore.tools;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Arrays;
import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * out/state.json - the same file every Python step reads and writes (common.py). Each step records
 * what it created, so later steps and teardown find resources by what was created, never by guessing.
 */
public final class State {
    static final ObjectMapper JSON = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
    public final Path path;
    private ObjectNode data;

    public State(Path path) throws IOException {
        this.path = path;
        this.data = Files.exists(path) ? (ObjectNode) JSON.readTree(path.toFile()) : JSON.createObjectNode();
    }

    /** AC_STATE, else agentcore/out/state.json found from the working directory upwards. */
    public static State locate() throws IOException {
        String env = System.getenv("AC_STATE");
        if (env != null && !env.isBlank()) return new State(Paths.get(env));
        for (Path d = Paths.get("").toAbsolutePath(); d != null; d = d.getParent()) {
            if (Files.exists(d.resolve("common.py")) && Files.isDirectory(d.resolve("06-agents"))) return new State(d.resolve("out/state.json"));
            Path ac = d.resolve("day4-orchestration-evals-cicd/agentcore");
            if (Files.exists(ac.resolve("common.py"))) return new State(ac.resolve("out/state.json"));
        }
        throw new IOException("cannot find agentcore/out/state.json - run from inside the agentcore folder, or set AC_STATE");
    }

    public JsonNode get(String key) { return data.get(key); }

    /** Like common.need(): fail with the step to run when a key is missing. */
    public JsonNode need(String key) {
        JsonNode v = data.get(key);
        if (v == null || v.isNull()) {
            throw new IllegalStateException("out/state.json has no '" + key + "' - run the earlier step that creates it first");
        }
        return v;
    }

    public List<String> missing(String... keys) {
        return Arrays.stream(keys).filter(k -> data.get(k) == null).toList();
    }

    public void save(String key, JsonNode value) throws IOException {
        data = Files.exists(path) ? (ObjectNode) JSON.readTree(path.toFile()) : data;   // merge with other writers
        data.set(key, value);
        Files.createDirectories(path.getParent());
        JSON.writeValue(path.toFile(), data);
        try { Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------")); }
        catch (UnsupportedOperationException ignored) { /* Windows */ }
    }

    public ObjectNode object() { return JSON.createObjectNode(); }
}
