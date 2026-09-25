// Posting a ticket comment needs the aira-ops/comment scope (the supervisor has it; investigator and reviewer don't).
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"ops-write___add_ticket_comment",
  resource == AgentCore::Gateway::"{GATEWAY_ARN}"
)
when { principal.hasTag("scope") && principal.getTag("scope") like "*aira-ops/comment*" };
