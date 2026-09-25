// Changing the concurrency cap needs the aira-ops/config scope - a HUMAN approver's client, never an agent's -
// and only up to the documented production maximum of 16 (runbook-ingest-backlog.md).
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"ops-write___set_ingest_concurrency",
  resource == AgentCore::Gateway::"{GATEWAY_ARN}"
)
when {
  principal.hasTag("scope") && principal.getTag("scope") like "*aira-ops/config*" &&
  context.input has value && context.input.value <= 16
};
