# Step 05 — Memory: remember the session, and what was learned

**Goal.** The supervisor remembers a conversation turn by turn (short-term) and keeps durable facts across
sessions (long-term), without you writing a database.

```
Memory <prefix>_ops_memory          events expire after 30 days
  short-term   every turn stored as an event under (actorId, sessionId)
  long-term    strategies run in the background (~1 min after a turn):
    semantic   "incident_facts"   -> /ops/{actorId}/facts                 facts about tickets and changes
    summary    "session_summary"  -> /ops/{actorId}/{sessionId}/summary   one running summary per session
```

## Do it

```bash
PYTHONPATH=.. python create_memory.py
```

Takes 1–3 minutes to become `ACTIVE`, then writes one smoke-test event and reads it back.

## How the supervisor uses it (step 6)

```python
AgentCoreMemorySessionManager(AgentCoreMemoryConfig(
    memory_id=..., actor_id=actor, session_id=session,
    retrieval_config={"/ops/{actorId}/facts": RetrievalConfig(top_k=5, relevance_score=0.3)}))
```

Passed to `Agent(session_manager=...)`: every message is saved as an event, and relevant long-term facts
are retrieved and injected before the model is called.

## Check it (after step 7)

```bash
PYTHONPATH=.. python -c "
from common import client, need
(mid,) = need('memory_id')
for r in client('bedrock-agentcore').list_memory_records(memoryId=mid, namespace='/ops/ops-team/facts')['memoryRecordSummaries']:
    print('-', r['content']['text'][:120])"
```

Then ask the supervisor about T-1001 in a **new** session (step 7) — it answers from memory without
calling a single tool.

## Talk about it

- Short-term memory is the conversation; long-term memory is what *should outlive* it. You choose what is
  extracted by choosing strategies, and where it goes by namespace.
- Memory is partitioned by `actorId`. The same memory resource can serve every team without leaking one
  team's facts into another's answers.
- Memory is data the agent will trust later — the guardrail and the "tool output is untrusted" rule apply
  to what goes in.
