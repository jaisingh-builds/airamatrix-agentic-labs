package com.airamatrix.day4.lab52;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A tiny argparse: {@code --name value} and {@code --name=value}, a fixed set of known options,
 * and a usage error (exit 2, like argparse) for anything else. No dependency.
 */
final class Args {
    static class UsageError extends RuntimeException {
        UsageError(String m) { super(m); }
    }

    private final Map<String, String> values = new LinkedHashMap<>();
    private final List<String> positional = new java.util.ArrayList<>();
    boolean help;

    /** known: option names (without --) that take a value. */
    static Args parse(String[] argv, int from, List<String> known) {
        Args a = new Args();
        for (int i = from; i < argv.length; i++) {
            String s = argv[i];
            if (s.equals("-h") || s.equals("--help")) { a.help = true; continue; }
            if (!s.startsWith("--")) { a.positional.add(s); continue; }
            String name = s.substring(2), value = null;
            int eq = name.indexOf('=');
            if (eq >= 0) { value = name.substring(eq + 1); name = name.substring(0, eq); }
            if (!known.contains(name)) throw new UsageError("unrecognized arguments: " + s);
            if (value == null) {
                if (i + 1 >= argv.length) throw new UsageError("argument --" + name + ": expected one argument");
                value = argv[++i];
            }
            a.values.put(name, value);
        }
        return a;
    }

    List<String> positional() { return positional; }

    String str(String name, String dflt) { return values.getOrDefault(name, dflt); }

    Integer integer(String name, Integer dflt) {
        String v = values.get(name);
        if (v == null) return dflt;
        try { return Integer.parseInt(v); }
        catch (NumberFormatException e) { throw new UsageError("argument --" + name + ": invalid int value: '" + v + "'"); }
    }

    Double dbl(String name, Double dflt) {
        String v = values.get(name);
        if (v == null) return dflt;
        try { return Double.parseDouble(v); }
        catch (NumberFormatException e) { throw new UsageError("argument --" + name + ": invalid float value: '" + v + "'"); }
    }
}
