#!/usr/bin/env node
// AgentCore mode (needs `npm install` in this folder once; local mode needs nothing).
//
//   node agentcore/cli.mjs deploy                                          package, upload, role + runtime (NODE_22)
//   node agentcore/cli.mjs invoke --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
//   node agentcore/cli.mjs approve RUN --by NAME --reason WHY              (who = your AWS identity + name)
//   node agentcore/cli.mjs gate-check                                      (the agent identity is DENIED a write)
//   node agentcore/cli.mjs eval [--repeat N] [--cases a,b] [--budget USD]
//   node agentcore/cli.mjs score [--last N]                                AgentCore Evaluations over the runtime's GenAI spans
//   node agentcore/cli.mjs teardown --yes
//   show / list / trace / reject: the local CLI's commands, same store.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GetCallerIdentityCommand, STSClient } from "@aws-sdk/client-sts";
import { Opts, main as localMain, show } from "../capstone.mjs";
import * as evals from "../lib/evals.mjs";
import { GateError, decide } from "../lib/gate.mjs";
import { SetupError, solution } from "../lib/repo.mjs";
import { Tracer, redact } from "../lib/spans.mjs";
import { Refused, Store, newId } from "../lib/store.mjs";
import { InvalidInstant, parseInstant } from "../lib/time.mjs";
import { AwsEnv, NODE, deploy, gateCheck, score, teardown } from "./aws.mjs";
import { call, evalTarget, record } from "./invoke.mjs";

export const USAGE = `node agentcore/cli.mjs <command>      (AgentCore mode; AC_PREFIX, AWS_REGION, AC_MODEL_ID)
  deploy                                              package + upload + role + runtime, reusing the shared stack (read-only)
  invoke   --account ACC --as-of ISO [--question T]   one run on AgentCore, stored and traced locally
  approve  RUN --by NAME --reason WHY                 (reject too) who = your AWS identity + name
  gate-check                                          the agent identity is DENIED a write at the Gateway
  eval     [--repeat N] [--cases a,b] [--budget USD]  the golden set against the deployed runtime
  score    [--last N]                                 AgentCore Evaluations (batch) over the last N sessions' GenAI spans
  teardown --yes                                      delete what capstone-state.json lists
  show RUN | list | trace RUN`;

export async function main(argv) {
  if (!argv.length || argv[0].startsWith("-h")) { console.log(USAGE); return argv.length ? 0 : 2; }
  const cmd = argv[0];
  if (["show", "list", "trace"].includes(cmd)) return localMain(argv);
  const yes = argv.includes("--yes");
  const db = process.env.CAPSTONE_DB || "capstone-runs.sqlite";
  let store;
  try {
    const o = new Opts(argv.slice(1).filter((a) => a !== "--yes"));
    switch (cmd) {
      case "deploy": await deploy(await AwsEnv.load()); return 0;
      case "invoke": {
        const env = await AwsEnv.load();
        const acc = o.need("account"), asOf = parseInstant(o.need("as-of")).toString();
        const t0 = Date.now();
        const r = await call(env, acc, asOf, o.get("question"), o.getOr("actor", "duty-manager"), newId());
        store = await Store.open(db);
        const trace = record(store, r, o.get("question"));
        console.log(`run ${r.run_id} on AgentCore · ${Math.round((Date.now() - t0) / 1000)}s · trace ${trace}`);
        show(store, r.run_id, (l) => console.log(l));
        return r.status === "blocked" ? 3 : r.status === "failed" || r.status === "guardrail_intervened" ? 1 : 0;
      }
      case "approve": case "reject": {
        const env = await AwsEnv.load();
        const { Arn } = await new STSClient({ region: env.region }).send(new GetCallerIdentityCommand({}));
        store = await Store.open(db);
        const rid = store.run(o.positional[0] ?? "").id;
        decide(store, rid, cmd, o.get("by"), Arn, o.get("reason"), new Tracer("capstone", rid));
        show(store, rid, (l) => console.log(l));
        return 0;
      }
      case "gate-check": return await gateCheck(await AwsEnv.load());
      case "score": return await score(await AwsEnv.load(), o.intOr("last", 5));
      case "eval": {
        const env = await AwsEnv.load();
        const golden = evals.load(o.getOr("golden", path.join(solution(), "golden", "cases.json")));
        const cases = evals.select(golden, o.get("cases"));
        if (!cases.length) { console.log("no matching cases"); return 2; }
        return await evals.execute(golden, cases, { repeat: o.intOr("repeat", 1), workers: o.intOr("workers", 3), budget: o.dblOr("budget", 1.5),
          target: "agentcore", runner: evalTarget(env), outDir: o.getOr("out", path.join(NODE, "results")) });
      }
      case "teardown":
        if (!yes) { console.error("teardown deletes the capstone runtime, role and bucket: add --yes"); return 3; }
        await teardown(await AwsEnv.load());
        return 0;
      default: console.error(`unknown command ${cmd}\n${USAGE}`); return 2;
    }
  } catch (e) {
    if (e instanceof GateError || e instanceof Refused || e instanceof InvalidInstant) { console.error(`refused: ${e.message}`); return 3; }
    if (e instanceof SetupError) { console.error(e.message); return 2; }
    console.error(`${e.name || "Error"}: ${redact(String(e.message), 0)}`);
    return 1;
  } finally {
    store?.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; });
}
