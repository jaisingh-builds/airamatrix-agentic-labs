package com.airamatrix.capstone.aws;

import java.nio.file.Paths;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.capstone.Cli;
import com.airamatrix.capstone.Evals;
import com.airamatrix.capstone.Gate;
import com.airamatrix.capstone.Repo;
import com.airamatrix.capstone.Sla;
import com.airamatrix.capstone.Store;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;

import software.amazon.awssdk.services.sts.StsClient;

/**
 * AgentCore mode.
 *
 *   java -jar java/aws-tools/target/capstone-aws.jar ecr-repo
 *   java -jar java/aws-tools/target/capstone-aws.jar deploy --image <account>.dkr.ecr.<region>.amazonaws.com/aira-d4-capstone-java:v1
 *   java -jar java/aws-tools/target/capstone-aws.jar invoke --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
 *   java -jar java/aws-tools/target/capstone-aws.jar approve RUN --by NAME --reason WHY    (who = your AWS identity + name)
 *   java -jar java/aws-tools/target/capstone-aws.jar gate-check                           (the agent identity is DENIED a write)
 *   java -jar java/aws-tools/target/capstone-aws.jar eval [--repeat N] [--cases a,b] [--budget USD]
 *   java -jar java/aws-tools/target/capstone-aws.jar teardown --yes
 *   show / list / trace / reject: the local CLI's commands, same store.
 */
public final class CapstoneAws {
    public static void main(String[] argv) {
        List<String> args = Arrays.asList(argv);
        if (args.isEmpty() || args.get(0).startsWith("-h")) { System.out.println(usage()); System.exit(args.isEmpty() ? 2 : 0); }
        Map<String, String> o = new HashMap<>();
        java.util.ArrayList<String> pos = new java.util.ArrayList<>();
        for (int i = 1; i < args.size(); i++) {
            if (args.get(i).startsWith("--")) {
                if (args.get(i).equals("--yes")) { o.put("yes", "1"); continue; }
                if (i + 1 >= args.size()) { System.err.println(args.get(i) + " needs a value"); System.exit(3); }
                o.put(args.get(i).substring(2), args.get(++i));
            } else pos.add(args.get(i));
        }
        String db = System.getenv().getOrDefault("CAPSTONE_DB", Cli.defaultDb());
        try {
            switch (args.get(0)) {
                case "ecr-repo" -> new Deploy(AwsEnv.load()).ecrRepo();
                case "deploy" -> new Deploy(AwsEnv.load()).deploy(need(o, "image"));
                case "invoke" -> {
                    AwsEnv env = AwsEnv.load();
                    String acc = need(o, "account"), asOf = Sla.iso(Sla.parseInstant(need(o, "as-of")));
                    long t0 = System.nanoTime();
                    JsonNode r = Invoke.call(env, acc, asOf, o.get("question"), o.getOrDefault("actor", "duty-manager"), Store.newId());
                    try (Store store = new Store(db)) {
                        java.nio.file.Path trace = Invoke.record(store, r, o.get("question"));
                        System.out.println("run " + r.path("run_id").asText() + " on AgentCore · " + Math.round((System.nanoTime() - t0) / 1e9) + "s · trace " + trace);
                        Cli.show(store, r.path("run_id").asText(), System.out);
                    }
                    String st = r.path("status").asText();
                    System.exit(st.equals("blocked") ? 3 : st.equals("failed") || st.equals("guardrail_intervened") ? 1 : 0);
                }
                case "approve", "reject" -> {
                    String principal;
                    try (StsClient sts = StsClient.builder().region(AwsEnv.load().awsRegion()).build()) { principal = sts.getCallerIdentity().arn(); }
                    try (Store store = new Store(db)) {
                        String rid = pos.isEmpty() ? "" : pos.get(0);
                        Gate.decide(store, store.run(rid).id(), args.get(0), o.get("by"), principal, o.get("reason"), new Spans("capstone", rid));
                        Cli.show(store, rid, System.out);
                    }
                }
                case "gate-check" -> System.exit(GateCheck.run(AwsEnv.load()));
                case "eval" -> {
                    AwsEnv env = AwsEnv.load();
                    JsonNode golden = Evals.load(Paths.get(o.getOrDefault("golden", Repo.solution().resolve("golden/cases.json").toString())));
                    System.exit(Evals.execute(golden, Evals.select(golden, o.get("cases")), Integer.parseInt(o.getOrDefault("repeat", "1")),
                            Integer.parseInt(o.getOrDefault("workers", "3")), Double.parseDouble(o.getOrDefault("budget", "1.5")), "agentcore",
                            Invoke.evalTarget(env), Paths.get(o.getOrDefault("out", Repo.java().resolve("results").toString())), System.out));
                }
                case "teardown" -> {
                    if (!o.containsKey("yes")) { System.err.println("teardown deletes the capstone runtime, role and image repository: add --yes"); System.exit(3); }
                    Teardown.run(AwsEnv.load());
                }
                case "show", "list", "trace" -> System.exit(Cli.run(args, System.getenv(), System.out, System.err));
                default -> { System.err.println("unknown command " + args.get(0) + "\n" + usage()); System.exit(2); }
            }
        } catch (Gate.GateError | IllegalArgumentException e) {
            System.err.println("refused: " + e.getMessage());
            System.exit(3);
        } catch (IllegalStateException e) {
            System.err.println(e.getMessage());
            System.exit(2);
        } catch (Exception e) {
            System.err.println(e.getClass().getSimpleName() + ": " + Spans.redact(String.valueOf(e.getMessage())));
            System.exit(1);
        }
    }

    static String need(Map<String, String> o, String k) {
        String v = o.get(k);
        if (v == null || v.isBlank()) throw new IllegalArgumentException("--" + k + " is required");
        return v;
    }

    static String usage() {
        return """
            java -jar java/aws-tools/target/capstone-aws.jar <command>      (AgentCore mode; AC_PREFIX, AWS_REGION, AC_MODEL_ID)
              ecr-repo                                            create the image repository (once)
              deploy   --image <ecr uri:tag>                      role + runtime, reusing the shared stack (read-only)
              invoke   --account ACC --as-of ISO [--question T]   one run on AgentCore, stored and traced locally
              approve  RUN --by NAME --reason WHY                 (reject too) who = your AWS identity + name
              gate-check                                          the agent identity is DENIED a write at the Gateway
              eval     [--repeat N] [--cases a,b] [--budget USD]  the golden set against the deployed runtime
              teardown --yes                                      delete what capstone-state.json lists
              show RUN | list | trace RUN""";
    }
}
