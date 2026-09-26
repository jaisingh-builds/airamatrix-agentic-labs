package com.airamatrix.agentcore.tools;

import java.util.Arrays;
import java.util.List;

/**
 * java -jar target/agentcore-tools.jar <command>
 *
 *   ecr-repo                                   create the image repository (once)
 *   deploy --image <ecr uri:tag>               create/update the three Java runtimes
 *   invoke <agent> "<prompt>" [--session ID] [--actor ID]
 *   approve --value N --version N [--as approver|supervisor|investigator]
 *
 * AC_PREFIX / AWS_REGION / AC_MODEL_ID as for the Python steps; state in agentcore/out/state.json.
 */
public final class AgentCoreTool {
    public static void main(String[] argv) {
        List<String> args = Arrays.asList(argv);
        if (args.isEmpty() || args.get(0).equals("-h") || args.get(0).equals("--help")) {
            System.out.println(usage());
            System.exit(args.isEmpty() ? 2 : 0);
        }
        try {
            List<String> rest = args.subList(1, args.size());
            switch (args.get(0)) {
                case "ecr-repo" -> new Deploy(Env.load(), State.locate()).ecrRepo();
                case "deploy" -> {
                    if (rest.size() != 2 || !rest.get(0).equals("--image")) throw new IllegalArgumentException("usage: deploy --image <ecr uri:tag>");
                    new Deploy(Env.load(), State.locate()).deploy(rest.get(1));
                }
                case "invoke" -> { Invoke.Request r = Invoke.parse(rest); Invoke.run(Env.load(), State.locate(), r); }
                case "approve" -> { Approve.Request r = Approve.parse(rest); Approve.run(Env.load(), State.locate(), r); }
                default -> throw new IllegalArgumentException("unknown command " + args.get(0) + "\n" + usage());
            }
        } catch (IllegalArgumentException | IllegalStateException e) {
            System.err.println(e.getMessage());
            System.exit(2);
        } catch (Exception e) {
            System.err.println(e.getClass().getSimpleName() + ": " + e.getMessage());
            System.exit(1);
        }
    }

    static String usage() {
        return """
            java -jar target/agentcore-tools.jar <command>
              ecr-repo                                   create the image repository (once)
              deploy --image <ecr uri:tag>               create/update the three Java runtimes
              invoke <agent> "<prompt>" [--session ID] [--actor ID]
              approve --value N --version N [--as approver|supervisor|investigator]""";
    }
}
