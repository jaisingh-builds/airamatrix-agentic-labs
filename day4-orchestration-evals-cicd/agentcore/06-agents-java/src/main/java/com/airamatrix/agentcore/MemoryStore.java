package com.airamatrix.agentcore;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient;
import software.amazon.awssdk.services.bedrockagentcore.model.Content;
import software.amazon.awssdk.services.bedrockagentcore.model.Conversational;
import software.amazon.awssdk.services.bedrockagentcore.model.CreateEventRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.Event;
import software.amazon.awssdk.services.bedrockagentcore.model.ListEventsRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.MemoryRecordSummary;
import software.amazon.awssdk.services.bedrockagentcore.model.PayloadType;
import software.amazon.awssdk.services.bedrockagentcore.model.RetrieveMemoryRecordsRequest;
import software.amazon.awssdk.services.bedrockagentcore.model.Role;
import software.amazon.awssdk.services.bedrockagentcore.model.SearchCriteria;

/**
 * AgentCore Memory for the supervisor - what AgentCoreMemorySessionManager does for the Python agent:
 *   short-term: every run is an event under (actorId, sessionId) - the prompt, the assistant's text AND the
 *               tool results. Tool results go in as USER turns, as Strands stores them (a Converse toolResult
 *               lives in a user message): the semantic extractor learns mostly from user-role content, and the
 *               specialists' findings are the facts worth keeping. Replayed history skips them;
 *   long-term:  facts the semantic strategy extracted from past sessions (namespace /ops/{actorId}/facts)
 *               are retrieved by relevance to the new prompt and given to the model as context.
 */
public class MemoryStore {
    private final BedrockAgentCoreClient client;
    private final String memoryId;

    public record Turn(String role, String text) {}

    /** Marks a tool result stored as a USER turn, so history replay can leave it out. */
    static final String TOOL_PREFIX = "[tool] ";

    public MemoryStore(BedrockAgentCoreClient client, String memoryId) {
        this.client = client;
        this.memoryId = memoryId;
    }

    /** Relevant long-term facts for this actor. */
    public List<String> facts(String actor, String query) {
        try {
            return client.retrieveMemoryRecords(RetrieveMemoryRecordsRequest.builder()
                    .memoryId(memoryId).namespace("/ops/" + actor + "/facts")
                    .searchCriteria(SearchCriteria.builder().searchQuery(query).topK(5).build()).build())
                    .memoryRecordSummaries().stream()
                    .filter(r -> r.score() == null || r.score() >= 0.3)
                    .map(MemoryRecordSummary::content).map(c -> c.text()).toList();
        } catch (Exception e) {
            return List.of();           // memory helps; it must never break a triage
        }
    }

    /** Earlier turns of this session, oldest first. */
    public List<Turn> history(String actor, String session) {
        List<Turn> turns = new ArrayList<>();
        try {
            List<Event> events = new ArrayList<>(client.listEvents(ListEventsRequest.builder()
                    .memoryId(memoryId).actorId(actor).sessionId(session).includePayloads(true).maxResults(50).build()).events());
            events.sort(Comparator.comparing(Event::eventTimestamp));
            for (Event e : events) {
                for (PayloadType p : e.payload()) {
                    Conversational c = p.conversational();
                    if (c == null || c.content() == null || c.content().text() == null) continue;
                    // tool results are for the extractor; the model is shown the conversation, not old tool output
                    if (c.role() == Role.USER && !c.content().text().startsWith(TOOL_PREFIX)) turns.add(new Turn("user", c.content().text()));
                    else if (c.role() == Role.ASSISTANT) turns.add(new Turn("assistant", c.content().text()));
                }
            }
        } catch (Exception e) { /* no history is fine */ }
        return turns;
    }

    public void save(String actor, String session, String userText, List<Turn> transcript) {
        client.createEvent(CreateEventRequest.builder().memoryId(memoryId).actorId(actor).sessionId(session)
                .eventTimestamp(Instant.now()).payload(payload(userText, transcript)).build());
    }

    /** The prompt, then the run's turns in order - at most 100 items (the CreateEvent limit). */
    static List<PayloadType> payload(String userText, List<Turn> transcript) {
        List<PayloadType> p = new ArrayList<>();
        p.add(item(Role.USER, userText));
        for (Turn t : transcript) {
            if (p.size() == 100) break;
            if (t.role().equals("tool")) p.add(item(Role.USER, TOOL_PREFIX + t.text()));
            else p.add(item(t.role().equals("assistant") ? Role.ASSISTANT : Role.USER, t.text()));
        }
        return p;
    }

    private static PayloadType item(Role role, String text) {
        return PayloadType.fromConversational(Conversational.builder().role(role)
                .content(Content.fromText(text.isEmpty() ? "(empty)" : text)).build());
    }
}
