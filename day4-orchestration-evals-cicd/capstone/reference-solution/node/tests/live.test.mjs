// Live: one real run through the training gateway (about $0.03). Only with LAB_LIVE=1 - a live test costs money.
//   LAB_LIVE=1 node --test day4-orchestration-evals-cicd/capstone/reference-solution/node/tests
import assert from "node:assert/strict";
import { test } from "node:test";
import { Config, GatewayClient } from "../../../../../labkit/node/agentic-core.mjs";
import { ResponderAgent, localModel } from "../lib/agent.mjs";
import { gradeCase } from "../lib/checks.mjs";
import * as evals from "../lib/evals.mjs";
import { HttpOpsReader } from "../lib/ops.mjs";
import { PrivateOps } from "../lib/private-ops.mjs";
import { solution } from "../lib/repo.mjs";
import * as responder from "../lib/responder.mjs";
import { parseInstant } from "../lib/time.mjs";
import { tracer } from "./helpers.mjs";
import path from "node:path";

test("live: the backlog case end to end passes its golden checks", { skip: process.env.LAB_LIVE !== "1" && "set LAB_LIVE=1 (costs money)" }, async () => {
  const kase = evals.select(evals.load(path.join(solution(), "golden", "cases.json")), "backlog-acc1001")[0];
  const ops = await PrivateOps.start([kase.account]);
  try {
    const cfg = new Config().require();
    const agent = new ResponderAgent(localModel(new GatewayClient(cfg)), cfg.model, 10, 0.3, () => undefined);
    const o = await responder.run("live-1", kase.account, parseInstant(kase.as_of), kase.question,
      new HttpOpsReader(ops.url, ops.readTokens[kase.account]), agent, tracer());
    const g = gradeCase(kase, o.toJson());
    assert.ok(g.passed, JSON.stringify(g.checks.filter((c) => !c.passed)));
    assert.ok(o.costUsd < 0.3, `cost ${o.costUsd}`);
  } finally {
    await ops.close();
  }
});
