#!/usr/bin/env bash
# One-time, per account and region: send X-Ray trace segments to CloudWatch Logs (Transaction Search).
# AgentCore Runtime, Gateway and Memory spans - and AgentCore Evaluations - read from there.
set -euo pipefail
R="${AWS_REGION:-ap-south-1}"
ACC="$(aws sts get-caller-identity --query Account --output text)"
# Let X-Ray write spans into CloudWatch Logs. (zsh users: always ${ACC}, never $ACC: - zsh eats ":l".)
aws logs put-resource-policy --region "$R" --policy-name AgentCoreTransactionSearch --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"TransactionSearchXRayAccess\",\"Effect\":\"Allow\",
  \"Principal\":{\"Service\":\"xray.amazonaws.com\"},\"Action\":\"logs:PutLogEvents\",
  \"Resource\":[\"arn:aws:logs:${R}:${ACC}:log-group:aws/spans:*\",\"arn:aws:logs:${R}:${ACC}:log-group:/aws/application-signals/data:*\"],
  \"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"${ACC}\"}}}]}" > /dev/null
# The resource policy takes a few seconds to apply; retry until X-Ray accepts it.
for i in $(seq 1 18); do
  aws xray update-trace-segment-destination --region "$R" --destination CloudWatchLogs > /dev/null 2>&1 && break
  sleep 10
done
aws xray update-indexing-rule --region "$R" --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":100}}' > /dev/null
aws xray get-trace-segment-destination --region "$R" --output table
