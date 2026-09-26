// The Runtime's entry point - packaged as app.js at the zip root (direct code deploy, NODE_22 only runs .js).
//
// Telemetry first, then the server. AgentCore Runtime injects the settings the PYTHON ADOT distro expects
// (OTEL_RESOURCE_ATTRIBUTES with service.name and aws.log.group.names). The ADOT Node distro needs the AWS OTLP
// endpoints spelled out - the same defaults as agentcore/06-agents-java start.sh - so spans reach X-Ray and this
// runtime's own log group, where AgentCore Evaluations reads them. Without ADOT (laptop, tests) the spans are no-ops.
"use strict";
const region = process.env.AWS_REGION || "ap-south-1";
const attrs = (process.env.OTEL_RESOURCE_ATTRIBUTES || "").split(",");
const logGroup = (attrs.find((a) => a.startsWith("aws.log.group.names=")) || "").split("=")[1] || "";
const d = (k, v) => { if (!process.env[k]) process.env[k] = v; };
d("OTEL_TRACES_EXPORTER", "otlp");
d("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf");
d("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", `https://xray.${region}.amazonaws.com/v1/traces`);
if (logGroup) d("OTEL_EXPORTER_OTLP_TRACES_HEADERS", `x-aws-log-group=${logGroup},x-aws-log-stream=spans`);
d("OTEL_LOGS_EXPORTER", "otlp");
d("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "http/protobuf");
d("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", `https://logs.${region}.amazonaws.com/v1/logs`);
d("OTEL_METRICS_EXPORTER", "none");
d("OTEL_AWS_APPLICATION_SIGNALS_ENABLED", "false");
if (process.env.CAPSTONE_OTEL !== "off") {
  try {
    require("@aws/aws-distro-opentelemetry-node-autoinstrumentation/register");
    console.log(`aira-capstone start: traces -> ${process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT} (${logGroup || "aws/spans"})`);
  } catch (e) {
    console.error(`aira-capstone start: no ADOT (${e.code || e.message}) - GenAI spans are not exported; the JSONL trace still is`);
  }
}
import("./day4-orchestration-evals-cicd/capstone/reference-solution/node/agentcore/runtime/server.mjs")
  .then((m) => m.start())
  .catch((e) => { console.error(`aira-capstone start failed: ${e.stack || e}`); process.exit(1); });
