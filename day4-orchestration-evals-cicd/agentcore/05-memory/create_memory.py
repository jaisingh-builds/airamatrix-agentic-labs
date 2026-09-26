"""Step 5 - AgentCore Memory for the supervisor.

Short-term memory: every turn is stored as an event under (actorId, sessionId).
Long-term memory: two strategies extract durable records from those events in the background:
  semantic  -> facts about incidents ("ingest.max_concurrent_jobs was raised to 8 on T-1005")
  summary   -> one running summary per session, so a follow-up question needs no replay

    PYTHONPATH=.. python create_memory.py
"""
import time
from common import PREFIX, client, save, say, state, wait

ac = client("bedrock-agentcore-control")
name = PREFIX.replace("-", "_") + "_ops_memory"      # memory names allow letters, digits and _

STRATEGIES = [
    {"semanticMemoryStrategy": {
        "name": "incident_facts",
        "description": "Durable facts about tickets, config changes and their outcomes",
        "namespaces": ["/ops/{actorId}/facts"]}},
    {"summaryMemoryStrategy": {
        "name": "session_summary",
        "description": "Running summary of each triage session",
        "namespaces": ["/ops/{actorId}/{sessionId}/summary"]}},
]

mid = state().get("memory_id")
if not mid:
    existing = [m for m in ac.list_memories()["memories"] if m["id"].startswith(name)]
    if existing:
        mid = existing[0]["id"]
    else:
        mid = ac.create_memory(name=name, eventExpiryDuration=30,
                               description="Supervisor memory for the Day 4 AgentCore reference system",
                               memoryStrategies=STRATEGIES)["memory"]["id"]
say("memory  ", mid, "(waiting for ACTIVE - 1-3 minutes when new)")
m = wait(lambda: ac.get_memory(memoryId=mid)["memory"], ok=("ACTIVE",), what="memory", pause=10)
arn = m["arn"]
strategies = {s["name"]: s["strategyId"] for s in m.get("strategies", [])}
say("status  ", m["status"], "strategies:", ", ".join(strategies))
save(memory_id=mid, memory_arn=arn, memory_strategies=strategies)

# Smoke test: write one short-term event, read it back.
dp = client("bedrock-agentcore")
actor, session = "smoke-test", f"smoke-{int(time.time())}"
dp.create_event(memoryId=mid, actorId=actor, sessionId=session, eventTimestamp=time.time(),
                payload=[{"conversational": {"role": "USER",
                                             "content": {"text": "Smoke test: T-1005 is about DICOM metadata."}}}])
events = dp.list_events(memoryId=mid, actorId=actor, sessionId=session)["events"]
say("smoke   ", f"{len(events)} event(s) stored and read back for actor={actor}")
