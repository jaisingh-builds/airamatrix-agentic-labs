#!/bin/sh
# Container entrypoint (agentcore/06-agents-java start.sh). AgentCore Runtime injects the telemetry settings the
# PYTHON ADOT distro expects; the ADOT JAVA agent needs the AWS OTLP endpoints spelled out (it SigV4-signs them).
REGION="${AWS_REGION:-ap-south-1}"
LOG_GROUP=$(echo "$OTEL_RESOURCE_ATTRIBUTES" | tr ',' '\n' | sed -n 's/^aws\.log\.group\.names=//p' | head -1)
export OTEL_TRACES_EXPORTER="${OTEL_TRACES_EXPORTER:-otlp}"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="${OTEL_EXPORTER_OTLP_TRACES_PROTOCOL:-http/protobuf}"
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-https://xray.${REGION}.amazonaws.com/v1/traces}"
if [ -n "$LOG_GROUP" ] && [ -z "$OTEL_EXPORTER_OTLP_TRACES_HEADERS" ]; then
  export OTEL_EXPORTER_OTLP_TRACES_HEADERS="x-aws-log-group=${LOG_GROUP},x-aws-log-stream=spans"
fi
export OTEL_LOGS_EXPORTER="${OTEL_LOGS_EXPORTER:-otlp}"
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL="${OTEL_EXPORTER_OTLP_LOGS_PROTOCOL:-http/protobuf}"
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT="${OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:-https://logs.${REGION}.amazonaws.com/v1/logs}"
export OTEL_METRICS_EXPORTER="${OTEL_METRICS_EXPORTER:-none}"
export OTEL_AWS_APPLICATION_SIGNALS_ENABLED="${OTEL_AWS_APPLICATION_SIGNALS_ENABLED:-false}"
export LAB_TRACE_DIR="${LAB_TRACE_DIR:-/tmp/traces}"
echo "aira-capstone start: traces -> ${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT} (${LOG_GROUP:-aws/spans})"
exec java -javaagent:/otel/aws-opentelemetry-agent.jar -XX:+UseContainerSupport -XX:MaxRAMPercentage=75 \
     -cp @/app/jib-classpath-file "$(cat /app/jib-main-class-file)"
