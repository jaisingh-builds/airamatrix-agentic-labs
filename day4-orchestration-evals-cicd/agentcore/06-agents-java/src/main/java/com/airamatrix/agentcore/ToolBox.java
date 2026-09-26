package com.airamatrix.agentcore;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** The tools one agent can call: the Gateway's (filtered per caller by Cedar) plus, for the supervisor, its specialists. */
public interface ToolBox extends AutoCloseable {

    /** name, description, JSON schema of the input. */
    record Spec(String name, String description, Map<String, Object> inputSchema) {}

    /** What a tool returned. error=true is reported to the model as a failed tool result, never thrown. */
    record Outcome(String text, boolean error) {}

    List<Spec> specs();

    Outcome call(String name, Map<String, Object> input);

    @Override
    default void close() {}

    /** Several tool boxes as one; the first that owns a name handles it. */
    static ToolBox of(ToolBox... boxes) {
        return new ToolBox() {
            @Override public List<Spec> specs() {
                List<Spec> all = new ArrayList<>();
                for (ToolBox b : boxes) all.addAll(b.specs());
                return all;
            }
            @Override public Outcome call(String name, Map<String, Object> input) {
                for (ToolBox b : boxes) {
                    if (b.specs().stream().anyMatch(s -> s.name().equals(name))) return b.call(name, input);
                }
                return new Outcome("{\"error\": \"no tool named " + name + "\"}", true);
            }
            @Override public void close() { for (ToolBox b : boxes) b.close(); }
        };
    }
}
