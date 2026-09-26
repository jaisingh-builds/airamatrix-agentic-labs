// Anyone holding the aira-ops/read scope may use the read tools and the handbook.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"ops-read___search_tickets",
    AgentCore::Action::"ops-read___get_ticket",
    AgentCore::Action::"ops-read___lookup_account",
    AgentCore::Action::"ops-read___list_jobs",
    AgentCore::Action::"ops-read___list_config",
    AgentCore::Action::"ops-read___get_config",
    AgentCore::Action::"handbook___search_handbook"
  ],
  resource == AgentCore::Gateway::"{GATEWAY_ARN}"
)
when { principal.hasTag("scope") && principal.getTag("scope") like "*aira-ops/read*" };
