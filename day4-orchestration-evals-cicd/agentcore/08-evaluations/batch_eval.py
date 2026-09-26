"""Step 8b - BATCH evaluation against ground truth: the regression test for an agent.

    PYTHONPATH=.. python batch_eval.py                   # run golden.json through the supervisor, then score it
    PYTHONPATH=.. python batch_eval.py --rescore         # score the last golden run again (no agent calls)
    PYTHONPATH=.. python batch_eval.py --no-run --sessions triage-abc... triage-def...   # score any sessions

Online evaluation (8a) tells you how production is doing. This answers a different question: "is the NEW
version at least as good as the old one on the cases we know matter?" - run it before promoting a change,
exactly like the offline evals in Lab 4.3 gate a pull request.

Ground truth used here (see golden.json):
  expected_trajectory -> Builtin.TrajectoryInOrderMatch  (programmatic: the tools were called in this order)
  assertions          -> Builtin.GoalSuccessRate          (LLM judge: each statement holds for the session)
"""
import argparse, json, pathlib, time, uuid
from datetime import datetime, timedelta, timezone
from common import client, need, save, say, state

p = argparse.ArgumentParser()
p.add_argument("--no-run", action="store_true", help="do not invoke the agent; score --sessions as they are")
p.add_argument("--sessions", nargs="*", default=[])
p.add_argument("--rescore", action="store_true", help="re-score the last golden run, e.g. after a span-lag error")
a = p.parse_args()

(runtimes, online) = need("runtimes", "online_evals")
sup = online["supervisor"]
golden = json.loads((pathlib.Path(__file__).parent / "golden.json").read_text())["scenarios"]
rt = client("bedrock-agentcore", read_timeout=900, retries={"total_max_attempts": 1})
started = datetime.now(timezone.utc) - timedelta(minutes=1)

runs = []                                   # (session_id, scenario or None)
if a.rescore:
    by_id = {sc["id"]: sc for sc in golden}
    runs = [(sid, by_id[scid]) for sid, scid in state().get("last_golden_runs", [])]
elif not a.no_run:
    for sc in golden:
        sid = f"golden-{sc['id']}-{uuid.uuid4().hex[:12]}".ljust(33, "0")
        t0 = time.time()
        r = rt.invoke_agent_runtime(agentRuntimeArn=runtimes["supervisor"], runtimeSessionId=sid,
                                    runtimeUserId="eval-runner",
                                    payload=json.dumps({"prompt": sc["prompt"], "actor_id": "eval-runner"}).encode())
        out = json.loads(r["response"].read())
        say(f"ran      {sc['id']:<28} {time.time() - t0:4.0f}s  tools={out.get('tools_used')}")
        runs.append((sid, sc))
    save(last_golden_runs=[[sid, sc["id"]] for sid, sc in runs])
    # Spans reach CloudWatch in batches; scoring too early gives LogEventMissingException -> use --rescore
    say("waiting  180s for the spans to be indexed in CloudWatch")
    time.sleep(180)
runs += [(s, None) for s in a.sessions]
if not runs:
    raise SystemExit("nothing to evaluate - run without --no-run, or pass --sessions")

meta = []
for sid, sc in runs:
    m = {"sessionId": sid}
    if sc:
        m["testScenarioId"] = sc["id"]
        m["groundTruth"] = {"inline": {"assertions": [{"text": t} for t in sc["assertions"]],
                                       "expectedTrajectory": {"toolNames": sc["expected_trajectory"]}}}
    meta.append(m)

evaluators = ["Builtin.GoalSuccessRate", "Builtin.TrajectoryInOrderMatch", "Builtin.ToolSelectionAccuracy",
              "Builtin.Faithfulness"]
job = rt.start_batch_evaluation(
    batchEvaluationName=f"golden_{datetime.now():%Y%m%d_%H%M%S}",
    evaluators=[{"evaluatorId": e} for e in evaluators],
    dataSourceConfig={"cloudWatchLogs": {"serviceNames": [sup["service"]], "logGroupNames": [sup["log_group"]],
                                         "filterConfig": {"sessionIds": [s for s, _ in runs]}}},
    evaluationMetadata={"sessionMetadata": meta},
    clientToken=str(uuid.uuid4()))
jid = job["batchEvaluationId"]
say("batch   ", jid, "started - scoring", len(runs), "session(s)")
while True:
    r = rt.get_batch_evaluation(batchEvaluationId=jid)
    if r["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED"):
        break
    time.sleep(20)
say("status  ", r["status"], r.get("failureReason") or "")
res = r.get("evaluationResults", {})
say(f"sessions {res.get('numberOfSessionsCompleted', 0)} completed, {res.get('numberOfSessionsFailed', 0)} failed "
    f"of {res.get('totalNumberOfSessions', 0)}")
for s in res.get("evaluatorSummaries", []):
    avg = s.get("statistics", {}).get("averageScore")
    say(f"  {s['evaluatorId']:<34} avg {avg if avg is None else round(avg, 2)}   "
        f"scored {s.get('totalEvaluated')}  failed {s.get('totalFailed')}")
dest = r.get("outputConfig", {}).get("cloudWatchConfig", {})
say("detail  ", dest.get("logGroupName"), "/", dest.get("logStreamName"), "(per-session scores + judge explanations)")
save(last_batch_eval=jid)
