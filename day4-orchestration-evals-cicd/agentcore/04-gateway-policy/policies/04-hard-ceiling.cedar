// Belt and braces (the validator rejects an unscoped principal here as 'overly restrictive' - scope it):
// nobody - approver included - sets the cap above 16 (the capacity-team limit) through this gateway. forbid always wins.
forbid(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"ops-write___set_ingest_concurrency",
  resource == AgentCore::Gateway::"{GATEWAY_ARN}"
)
when { context.input has value && context.input.value > 16 };
