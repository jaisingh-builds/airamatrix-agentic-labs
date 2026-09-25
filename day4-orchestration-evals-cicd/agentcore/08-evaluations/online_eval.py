"""Step 8a - ONLINE evaluation: score live production traffic continuously.

    PYTHONPATH=.. python online_eval.py

AgentCore Evaluations reads the OpenTelemetry traces the runtimes already write to CloudWatch (step 1 turned on
Transaction Search; opentelemetry-instrument in step 6 emits them). No code change in the agents.

  sampling 100%  - fine for a training account; production typically samples 5-20%
  session timeout 5 min - a session with no new spans for 5 minutes is "complete" and gets session-level scores
Scores land in CloudWatch: GenAI Observability -> Evaluations, and as metrics (step 9's dashboard).
"""
from common import ACCOUNT, PREFIX, REGION, client, need, role, save, say, state

(runtimes,) = need("runtimes")
ac = client("bedrock-agentcore-control")

# Which runtimes to watch: their log group and OTel service name are derived from the runtime id
ids = {n: arn.rsplit("/", 1)[1] for n, arn in runtimes.items()}

EVALUATORS = [
    "Builtin.GoalSuccessRate",         # session: did the user get what they asked for?
    "Builtin.ToolSelectionAccuracy",   # tool call: was this the right tool at this point?
    "Builtin.ToolParameterAccuracy",   # tool call: were the arguments right (e.g. the ticket id)?
    "Builtin.Faithfulness",            # trace: is the answer supported by what the tools returned?
    "Builtin.Helpfulness",             # trace
    "Builtin.Harmfulness",             # trace: safety
]

rarn = role(f"{PREFIX}-evaluation", "bedrock-agentcore.amazonaws.com", {"Version": "2012-10-17", "Statement": [
    {"Sid": "ReadTraces", "Effect": "Allow",
     "Action": ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StartQuery"], "Resource": "*"},
    {"Sid": "WriteResults", "Effect": "Allow",
     "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/evaluations/*"},
    {"Sid": "IndexSpans", "Effect": "Allow", "Action": ["logs:DescribeIndexPolicies", "logs:PutIndexPolicy"],
     "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:aws/spans",
                  f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:aws/spans:*"]},
    {"Sid": "JudgeModel", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
     "Resource": [f"arn:aws:bedrock:{REGION}::foundation-model/*", f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/*"]},
]}, extra_trust={"Condition": {
    "StringEquals": {"aws:SourceAccount": ACCOUNT, "aws:ResourceAccount": ACCOUNT},
    "ArnLike": {"aws:SourceArn": [f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:evaluator/*",
                                  f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:online-evaluation-config/*"]}}})

configs = {}
for agent, rid in ids.items():                 # one config per agent: a config watches exactly one service
    lg = f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"
    svc = f"{rid.rsplit('-', 1)[0]}.DEFAULT"                                   # e.g. aira_d4_supervisor.DEFAULT
    cname = f"{PREFIX.replace('-', '_')}_{agent}_eval"
    spec = dict(rule={"samplingConfig": {"samplingPercentage": 100.0}, "sessionConfig": {"sessionTimeoutMinutes": 5}},
                dataSourceConfig={"cloudWatchLogs": {"logGroupNames": [lg], "serviceNames": [svc]}},
                evaluators=[{"evaluatorId": e} for e in EVALUATORS],
                evaluationExecutionRoleArn=rarn, description=f"Scores every {agent} session and tool call")
    existing = [c for c in ac.list_online_evaluation_configs()["onlineEvaluationConfigs"]
                if c["onlineEvaluationConfigName"] == cname]
    if existing:
        cid = existing[0]["onlineEvaluationConfigId"]
        ac.update_online_evaluation_config(onlineEvaluationConfigId=cid, **spec)
    else:
        cid = ac.create_online_evaluation_config(onlineEvaluationConfigName=cname, enableOnCreate=True,
                                                 **spec)["onlineEvaluationConfigId"]
    c = ac.get_online_evaluation_config(onlineEvaluationConfigId=cid)
    out = c.get("outputConfig", {}).get("cloudWatchConfig", {})
    say(f"online   {agent:<12} {cid}  {c.get('status')}/{c.get('executionStatus')}  results -> {out.get('logGroupName')}")
    configs[agent] = {"id": cid, "arn": c["onlineEvaluationConfigArn"], "service": svc, "log_group": lg,
                      "results_log_group": out.get("logGroupName")}
say("scoring ", ", ".join(EVALUATORS))
save(online_evals=configs, eval_role=rarn)
