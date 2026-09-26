// AgentCore mode, the AWS side: settings, the two state files, deploy, gate-check, teardown.
//   shared: agentcore/out/state.json (AC_STATE) - the Day 4 stack's guardrail, identity, gateway. READ ONLY here.
//   own:    reference-solution/node/out/capstone-state.json (CAPSTONE_STATE) - what THIS tool created, for teardown.
// Account ids, ARNs and ids live only in these gitignored files, never in the repo.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BedrockAgentCoreControlClient, CreateAgentRuntimeCommand, DeleteAgentRuntimeCommand, GetAgentRuntimeCommand,
  ListAgentRuntimesCommand, UpdateAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore-control";
import { CloudWatchLogsClient, DeleteLogGroupCommand } from "@aws-sdk/client-cloudwatch-logs";
import { CognitoIdentityProviderClient, DescribeUserPoolClientCommand } from "@aws-sdk/client-cognito-identity-provider";
import { CreateRoleCommand, DeleteRoleCommand, DeleteRolePolicyCommand, GetRoleCommand, IAMClient, PutRolePolicyCommand,
  UpdateAssumeRolePolicyCommand } from "@aws-sdk/client-iam";
import { CreateBucketCommand, DeleteBucketCommand, DeleteObjectsCommand, HeadBucketCommand, ListObjectVersionsCommand,
  PutBucketEncryptionCommand, PutObjectCommand, PutPublicAccessBlockCommand, S3Client } from "@aws-sdk/client-s3";
import { GetCallerIdentityCommand, STSClient } from "@aws-sdk/client-sts";
import { SetupError } from "../lib/repo.mjs";
import { tree, zip } from "./zip.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const NODE = path.resolve(HERE, "..");
const REPO = path.resolve(NODE, "../../../..");
const SOL_REL = "day4-orchestration-evals-cicd/capstone/reference-solution/node";

export const say = (s) => console.log(`  ${s}`);
const or = (k, d) => (process.env[k] && process.env[k].trim() ? process.env[k] : d);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class AwsEnv {
  static async load() {
    const region = or("AWS_REGION", "ap-south-1");
    const { Account } = await new STSClient({ region }).send(new GetCallerIdentityCommand({}));
    return new AwsEnv({ region, account: Account, prefix: or("AC_PREFIX", "aira-d4"), modelId: or("AC_MODEL_ID", "global.anthropic.claude-sonnet-5"),
      sharedPath: or("AC_STATE", path.join(REPO, "day4-orchestration-evals-cicd/agentcore/out/state.json")),
      ownPath: or("CAPSTONE_STATE", path.join(NODE, "out/capstone-state.json")) });
  }

  constructor(o) { Object.assign(this, o); }

  get p_() { return this.prefix.replace(/-/g, "_"); }
  /** aira_d4cap_node_responder - next to the Java (aira_d4cap_java_responder) and Python (aira_d4cap_py_*) runtimes. */
  get runtimeName() { return `${this.p_}cap_node_responder`; }
  get roleName() { return `${this.prefix}-capstone-node-runtime`; }
  get bucketName() { return `${this.prefix}-capstone-node-${this.account}-${this.region}`; }

  shared() {
    if (!fs.existsSync(this.sharedPath)) throw new SetupError(`no ${this.sharedPath} - the Day 4 AgentCore stack (steps 02-05) must exist; set AC_STATE`);
    return JSON.parse(fs.readFileSync(this.sharedPath, "utf8"));
  }

  need(dotted) {
    let n = this.shared();
    for (const k of dotted.split(".")) n = n?.[k];
    if (n == null || (typeof n === "string" && !n.trim())) {
      throw new SetupError(`agentcore/out/state.json has no '${dotted}' - run the AgentCore step that creates it`);
    }
    return n;
  }

  investigatorScopes() { return this.need("clients.investigator.scopes"); }

  own() { return fs.existsSync(this.ownPath) ? JSON.parse(fs.readFileSync(this.ownPath, "utf8")) : {}; }

  saveOwn(key, value) {
    const o = this.own();
    if (value == null) delete o[key]; else o[key] = value;
    fs.mkdirSync(path.dirname(this.ownPath), { recursive: true });
    fs.writeFileSync(this.ownPath, JSON.stringify(o, null, 2) + "\n", { mode: 0o600 });
    try { fs.chmodSync(this.ownPath, 0o600); } catch { /* Windows */ }
  }
}

/** Exactly what the runtime's settings() reads. */
export function environment(env) {
  return { AWS_REGION: env.region, GATEWAY_URL: env.need("gateway_url"), OAUTH_PROVIDER: env.need("providers.investigator.name"),
    OAUTH_SCOPES: env.investigatorScopes().join(" "), MODEL_ID: env.modelId, GUARDRAIL_ID: env.need("guardrail_id"),
    GUARDRAIL_VERSION: String(env.need("guardrail_version")), MAX_BUDGET_USD: or("CAPSTONE_MAX_BUDGET_USD", "0.40"),
    MAX_TURNS: or("CAPSTONE_MAX_TURNS", "10"), LAB_TRACE_DIR: "/tmp/traces", AGENT_OBSERVABILITY_ENABLED: "true" };
}

const stmt = (Sid, Action, Resource) => ({ Sid, Effect: "Allow", Action, Resource });

/** The runtime role's one inline policy. Pure, so it is tested offline. No gateway write, no Memory, no other runtime. */
export function policy(env) {
  const { account: a, region: r } = env, rt = `arn:aws:bedrock-agentcore:${r}:${a}`;
  return { Version: "2012-10-17", Statement: [
    stmt("Model", ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:::foundation-model/*", `arn:aws:bedrock:*:${a}:inference-profile/*`]),
    stmt("Guardrail", "bedrock:ApplyGuardrail", `arn:aws:bedrock:${r}:${a}:guardrail/${env.need("guardrail_id")}`),
    stmt("Identity", ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"],
      [`${rt}:workload-identity-directory/default`, `${rt}:workload-identity-directory/default/*`, `${rt}:token-vault/default`,
        env.need("providers.investigator.arn")]),
    stmt("ProviderSecret", "secretsmanager:GetSecretValue",
      `arn:aws:secretsmanager:${r}:${a}:secret:bedrock-agentcore-identity!default/oauth2/${env.need("providers.investigator.name")}*`),
    stmt("Logs", ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
      `arn:aws:logs:${r}:${a}:log-group:*`),
    stmt("Traces", ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], "*"),
    { ...stmt("Metrics", "cloudwatch:PutMetricData", "*"), Condition: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } } },
  ] };          // no S3: the service reads the package with the deployer's credentials at create/update time
}

export const trust = (account) => ({ Version: "2012-10-17", Statement: [{ Effect: "Allow",
  Principal: { Service: "bedrock-agentcore.amazonaws.com" }, Action: "sts:AssumeRole", Condition: { StringEquals: { "aws:SourceAccount": account } } }] });

/** The deployment package: app.js at the root, the code at its repo path (relative imports keep working), node_modules. */
export function buildPackage(outFile) {
  const rt = path.join(NODE, "agentcore/runtime");
  if (!fs.existsSync(path.join(rt, "node_modules"))) {
    say("build    npm install (runtime dependencies, once)");
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["install", "--omit=dev", "--no-audit", "--no-fund", "--loglevel=error"],
      { cwd: rt, stdio: "inherit", shell: process.platform === "win32" });
  }
  const code = (rel) => ({ name: `${SOL_REL}/${rel}`, data: fs.readFileSync(path.join(NODE, rel)) });
  const entries = [
    { name: "app.js", data: fs.readFileSync(path.join(rt, "app.cjs")) },
    { name: "labkit/node/agentic-core.mjs", data: fs.readFileSync(path.join(REPO, "labkit/node/agentic-core.mjs")) },
    code("contracts/sla-proposal.json"),
    ...fs.readdirSync(path.join(NODE, "lib")).filter((f) => f.endsWith(".mjs")).map((f) => code(`lib/${f}`)),
    ...fs.readdirSync(rt).filter((f) => f.endsWith(".mjs")).map((f) => code(`agentcore/runtime/${f}`)),
    ...tree(path.join(rt, "node_modules"), "node_modules/", (rel) => /\.(map|d\.ts|d\.mts|d\.cts|md|markdown)$/i.test(rel) || rel.startsWith(".")),
  ];
  const size = zip(entries, outFile);
  say(`build    ${path.basename(outFile)} ${(size / 1e6).toFixed(1)} MB, ${entries.length} files`);
  return crypto.createHash("sha256").update(fs.readFileSync(outFile)).digest("hex").slice(0, 12);
}

async function ensureBucket(s3, env) {
  const Bucket = env.bucketName;
  try { await s3.send(new HeadBucketCommand({ Bucket, ExpectedBucketOwner: env.account })); return; } catch (e) {
    if (e.$metadata?.httpStatusCode !== 404 && e.name !== "NotFound") throw e;
  }
  await s3.send(new CreateBucketCommand({ Bucket, CreateBucketConfiguration: { LocationConstraint: env.region } }));
  await s3.send(new PutPublicAccessBlockCommand({ Bucket, PublicAccessBlockConfiguration: { BlockPublicAcls: true, IgnorePublicAcls: true,
    BlockPublicPolicy: true, RestrictPublicBuckets: true } }));
  await s3.send(new PutBucketEncryptionCommand({ Bucket, ServerSideEncryptionConfiguration: { Rules: [{ ApplyServerSideEncryptionByDefault: { SSEAlgorithm: "AES256" } }] } }));
  say(`s3       created bucket ${Bucket}`);
}

async function ensureRole(env) {
  const iam = new IAMClient({ region: "us-east-1" });
  const name = env.roleName, doc = JSON.stringify(trust(env.account));
  let arn, fresh = false;
  try {
    arn = (await iam.send(new GetRoleCommand({ RoleName: name }))).Role.Arn;
    await iam.send(new UpdateAssumeRolePolicyCommand({ RoleName: name, PolicyDocument: doc }));
  } catch (e) {
    if (e.name !== "NoSuchEntityException") throw e;
    arn = (await iam.send(new CreateRoleCommand({ RoleName: name, AssumeRolePolicyDocument: doc, Description: "Day 4 capstone reference runtime (Node)" }))).Role.Arn;
    fresh = true;
  }
  await iam.send(new PutRolePolicyCommand({ RoleName: name, PolicyName: "least-privilege", PolicyDocument: JSON.stringify(policy(env)) }));
  say(`iam      role ${name} ${fresh ? "created" : "updated"}`);
  if (fresh) await sleep(12000);          // a new role takes a few seconds before a service can assume it
  return arn;
}

async function findRuntime(ac, name) {
  let nextToken;
  do {
    const r = await ac.send(new ListAgentRuntimesCommand({ maxResults: 100, nextToken }));
    const hit = (r.agentRuntimes || []).find((x) => x.agentRuntimeName === name);
    if (hit) return hit.agentRuntimeId;
    nextToken = r.nextToken;
  } while (nextToken);
  return null;
}

export async function deploy(env) {
  const vars = environment(env);                 // fails early if the shared stack is missing
  const pkg = path.join(NODE, "out/runtime.zip");
  fs.mkdirSync(path.dirname(pkg), { recursive: true });
  const digest = buildPackage(pkg);
  const s3 = new S3Client({ region: env.region });
  await ensureBucket(s3, env);
  env.saveOwn("bucket", env.bucketName);
  const key = `runtime/${digest}.zip`;           // a new key per build: the runtime picks up a change only when the artifact changes
  await s3.send(new PutObjectCommand({ Bucket: env.bucketName, Key: key, Body: fs.readFileSync(pkg), ExpectedBucketOwner: env.account }));
  say(`upload   s3://${env.bucketName}/${key}`);
  const roleArn = await ensureRole(env);
  env.saveOwn("role_name", env.roleName);
  const ac = new BedrockAgentCoreControlClient({ region: env.region });
  const spec = {
    agentRuntimeArtifact: { codeConfiguration: { code: { s3: { bucket: env.bucketName, prefix: key } }, runtime: "NODE_22", entryPoint: ["app.js"] } },
    roleArn, networkConfiguration: { networkMode: "PUBLIC" }, protocolConfiguration: { serverProtocol: "HTTP" }, environmentVariables: vars,
    description: "Day 4 capstone reference: SLA-breach responder (Node, direct code deploy)",
  };
  let id = await findRuntime(ac, env.runtimeName);
  if (id) {
    await ac.send(new UpdateAgentRuntimeCommand({ agentRuntimeId: id, ...spec }));
    say(`runtime  updating ${env.runtimeName}`);
  } else {
    id = (await ac.send(new CreateAgentRuntimeCommand({ agentRuntimeName: env.runtimeName, ...spec }))).agentRuntimeId;
    say(`runtime  creating ${env.runtimeName}`);
  }
  const rt = { id, name: env.runtimeName, log_group: `/aws/bedrock-agentcore/runtimes/${id}-DEFAULT` };
  env.saveOwn("runtime", rt);                    // recorded before the wait: teardown works even if it fails
  for (let i = 0; i < 90; i++) {
    const r = await ac.send(new GetAgentRuntimeCommand({ agentRuntimeId: id }));
    if (r.status === "READY") {
      env.saveOwn("runtime", { ...rt, arn: r.agentRuntimeArn });
      env.saveOwn("package", key);
      say(`runtime  READY    ${env.runtimeName}  (arn in ${path.basename(env.ownPath)})`);
      return;
    }
    if (r.status === "CREATE_FAILED" || r.status === "UPDATE_FAILED") throw new SetupError(`runtime ${r.status}: ${r.failureReason}`);
    await sleep(10000);
  }
  throw new SetupError("runtime still not ready");
}

/** Deletes exactly what capstone-state.json lists - the runtime, its log group, the role, the bucket. Never the shared stack. */
export async function teardown(env) {
  const own = env.own();
  if (own.runtime?.id) {
    const ac = new BedrockAgentCoreControlClient({ region: env.region });
    try {
      await ac.send(new DeleteAgentRuntimeCommand({ agentRuntimeId: own.runtime.id }));
      say(`runtime  deleted ${own.runtime.name}`);
      for (let i = 0; i < 30; i++) {
        try { await ac.send(new GetAgentRuntimeCommand({ agentRuntimeId: own.runtime.id })); await sleep(5000); } catch { break; }
      }
    } catch (e) { if (e.name === "ResourceNotFoundException") say("runtime  already gone"); else throw e; }
    try {
      await new CloudWatchLogsClient({ region: env.region }).send(new DeleteLogGroupCommand({ logGroupName: own.runtime.log_group }));
      say(`logs     deleted ${own.runtime.log_group}`);
    } catch (e) { if (e.name === "ResourceNotFoundException") say("logs     no log group"); else throw e; }
    env.saveOwn("runtime", null);
  }
  if (own.role_name) {
    const iam = new IAMClient({ region: "us-east-1" });
    try { await iam.send(new DeleteRolePolicyCommand({ RoleName: own.role_name, PolicyName: "least-privilege" })); } catch { /* none */ }
    try { await iam.send(new DeleteRoleCommand({ RoleName: own.role_name })); say(`iam      deleted role ${own.role_name}`); } catch (e) {
      if (e.name === "NoSuchEntityException") say("iam      role already gone"); else throw e;
    }
    env.saveOwn("role_name", null);
  }
  if (own.bucket) {
    const s3 = new S3Client({ region: env.region });
    try {
      for (;;) {
        const v = await s3.send(new ListObjectVersionsCommand({ Bucket: own.bucket }));
        const objs = [...(v.Versions || []), ...(v.DeleteMarkers || [])].map((o) => ({ Key: o.Key, VersionId: o.VersionId }));
        if (!objs.length) break;
        await s3.send(new DeleteObjectsCommand({ Bucket: own.bucket, Delete: { Objects: objs } }));
      }
      await s3.send(new DeleteBucketCommand({ Bucket: own.bucket }));
      say(`s3       deleted bucket ${own.bucket}`);
    } catch (e) { if (e.name === "NoSuchBucket") say("s3       bucket already gone"); else throw e; }
    env.saveOwn("bucket", null);
    env.saveOwn("package", null);
  }
  if (own.batch_evaluations?.length) {
    const { BedrockAgentCoreClient, DeleteBatchEvaluationCommand } = await import("@aws-sdk/client-bedrock-agentcore");
    const rt = new BedrockAgentCoreClient({ region: env.region });
    for (const id of own.batch_evaluations) {
      try { await rt.send(new DeleteBatchEvaluationCommand({ batchEvaluationId: id })); say(`eval     deleted batch evaluation ${id}`); } catch (e) {
        say(`eval     ${id}: ${e.name}`);
      }
    }
    env.saveOwn("batch_evaluations", null);
  }
  env.saveOwn("sessions", null);
  say("done     nothing of the shared Day 4 stack was touched");
}

/**
 * The AgentCore-native half of the approval point: prove at the Gateway that the AGENT's identity (the investigator
 * client this runtime uses) cannot write, whatever a prompt says. tools/list shows it no write tool, and a direct
 * tools/call ops-write___add_ticket_comment is DENIED by Cedar - nothing is written. The client secret is read from
 * Cognito into memory for this one token request and never printed or stored.
 */
export async function gateCheck(env) {
  const cid = env.need("clients.investigator.client_id"), scope = env.investigatorScopes().join(" ");
  const pool = await new CognitoIdentityProviderClient({ region: env.region }).send(new DescribeUserPoolClientCommand({ UserPoolId: env.need("user_pool"), ClientId: cid }));
  const basic = Buffer.from(`${cid}:${pool.UserPoolClient.ClientSecret}`).toString("base64");
  const tok = await fetch(env.need("token_url"), { method: "POST", signal: AbortSignal.timeout(20000),
    headers: { authorization: `Basic ${basic}`, "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", scope }) });
  if (tok.status !== 200) throw new SetupError(`token endpoint said ${tok.status}`);
  const token = (await tok.json()).access_token;
  say(`identity the agent's own client (investigator), scope '${scope}'`);
  const url = env.need("gateway_url");
  const list = await rpc(url, token, { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} });
  const names = (list.result?.tools || []).map((t) => t.name);
  const writes = names.filter((n) => n.startsWith("ops-write___")).length;
  say(`tools    tools/list shows ${names.length} tools, ${writes} write tools`);
  const call = await rpc(url, token, { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "ops-write___add_ticket_comment",
    arguments: { ticket_id: "T-1001", comment: "[capstone gate-check] this call must be denied", "Idempotency-Key": crypto.randomUUID() } } });
  if (call.error || call.result?.isError) {
    const msg = call.error ? call.error.message : call.result?.content?.[0]?.text ?? "";
    say(`DENIED   ${String(msg).slice(0, 220)}`);
    return writes === 0 ? 0 : 1;
  }
  say(`UNEXPECTED ALLOW - the agent identity could write through the Gateway: ${JSON.stringify(call.result)}`);
  return 1;
}

async function rpc(url, token, body) {
  const r = await fetch(url, { method: "POST", signal: AbortSignal.timeout(60000), body: JSON.stringify(body),
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json", accept: "application/json, text/event-stream" } });
  const raw = await r.text();
  const s = raw.indexOf("{"), e = raw.lastIndexOf("}");        // the body may be SSE-framed
  return s < 0 ? { error: { message: `HTTP ${r.status}` } } : JSON.parse(raw.slice(s, e + 1));
}

/**
 * AgentCore Evaluations over this runtime's GenAI spans (the ADOT export from app.js): a batch evaluation of the
 * last N sessions `invoke` / `eval` recorded. No ground truth here - the golden checks are the eval harness's job;
 * this shows the platform evaluators can read and score the Node agent. Spans are indexed in batches: run it 2-3
 * minutes after the invocations.
 */
export async function score(env, last = 5) {
  const { BedrockAgentCoreClient, GetBatchEvaluationCommand, StartBatchEvaluationCommand } = await import("@aws-sdk/client-bedrock-agentcore");
  const own = env.own();
  if (!own.runtime?.id) throw new SetupError(`no runtime in ${path.basename(env.ownPath)} - run deploy first`);
  const sessions = (own.sessions || []).filter((s) => s.status && s.status !== "guardrail_intervened").slice(-last).map((s) => s.session);
  if (!sessions.length) throw new SetupError("no recorded sessions with a proposal - run invoke first");
  const rt = new BedrockAgentCoreClient({ region: env.region });
  const evaluators = ["Builtin.GoalSuccessRate", "Builtin.ToolSelectionAccuracy", "Builtin.Faithfulness"];
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const job = await rt.send(new StartBatchEvaluationCommand({ batchEvaluationName: `capnode_${stamp}`, clientToken: crypto.randomUUID(),
    evaluators: evaluators.map((evaluatorId) => ({ evaluatorId })),
    dataSourceConfig: { cloudWatchLogs: { serviceNames: [`${own.runtime.name}.DEFAULT`], logGroupNames: [own.runtime.log_group],
      filterConfig: { sessionIds: sessions } } } }));
  const id = job.batchEvaluationId;
  env.saveOwn("batch_evaluations", [...(env.own().batch_evaluations || []), id]);
  say(`batch    ${id} started - scoring ${sessions.length} session(s)`);
  let r;
  for (;;) {
    r = await rt.send(new GetBatchEvaluationCommand({ batchEvaluationId: id }));
    if (["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED"].includes(r.status)) break;
    await sleep(20000);
  }
  say(`status   ${r.status} ${r.failureReason || ""}`);
  const res = r.evaluationResults || {};
  say(`sessions ${res.numberOfSessionsCompleted ?? 0} completed, ${res.numberOfSessionsFailed ?? 0} failed of ${res.totalNumberOfSessions ?? 0}`);
  for (const s of res.evaluatorSummaries || []) {
    const avg = s.statistics?.averageScore;
    say(`  ${s.evaluatorId.padEnd(34)} avg ${avg == null ? avg : Math.round(avg * 100) / 100}   scored ${s.totalEvaluated}  failed ${s.totalFailed}`);
  }
  return r.status === "COMPLETED" ? 0 : 1;
}
