package com.airamatrix.capstone;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * The SLA arithmetic - done in CODE, never by the model. The agent reads the result through the
 * sla_report tool, and the guardrail recomputes it from source to check every number the agent claims.
 *
 * Policy (the contract's SLA, in one place a reviewer can read):
 *   ticket target = contract_sla_minutes x {P1: 1, P2: 2, P3: 5}; P4 is not tracked
 *   job target    = contract_sla_minutes (turnaround), for queued and running jobs
 *   state         = breached if elapsed > target, at_risk if elapsed >= 75% of target, else ok
 *   the clock     = an explicit as_of instant, so the same data always gives the same answer
 *                   (the seed data is from 24 Sep 2026; evals and demos freeze the clock there)
 * Anything created after as_of did not exist yet and is left out.
 */
public final class Sla {
    public static final double AT_RISK = 0.75;
    public static final Map<String, Integer> PRIORITY_MULTIPLIER = Map.of("P1", 1, "P2", 2, "P3", 5);
    public static final Set<String> OPEN_TICKET = Set.of("open", "in_progress");
    public static final Set<String> ACTIVE_JOB = Set.of("queued", "running");
    static final int MAX_TICKETS = 25;

    private Sla() {}

    public record Item(String id, String kind, String priority, String status, String title, String startedAt,
                       long elapsedMinutes, long targetMinutes, int pct, String state, Integer slideCount) {}

    public record Report(String accountId, String accountName, String tier, int contractSlaMinutes, String asOf,
                         List<Item> items, List<String> untracked, Set<String> ticketIds, Set<String> jobIds) {

        /** Items at risk or breached, most urgent first. */
        public List<Item> exposed() { return items.stream().filter(i -> !i.state().equals("ok")).toList(); }

        public Item item(String id) { return items.stream().filter(i -> i.id().equals(id)).findFirst().orElse(null); }

        public boolean inScope(String id) { return ticketIds.contains(id) || jobIds.contains(id); }

        public ObjectNode toJson() {
            ObjectNode o = Contracts.object();
            o.put("account_id", accountId).put("account_name", accountName).put("tier", tier)
             .put("contract_sla_minutes", contractSlaMinutes).put("as_of", asOf);
            ArrayNode arr = o.putArray("items");
            for (Item i : items) {
                ObjectNode n = arr.addObject().put("item", i.id()).put("kind", i.kind()).put("status", i.status())
                        .put("started_at", i.startedAt()).put("elapsed_minutes", i.elapsedMinutes())
                        .put("target_minutes", i.targetMinutes()).put("pct_of_target", i.pct()).put("state", i.state());
                if (i.priority() != null) n.put("priority", i.priority());
                if (i.title() != null) n.put("title", i.title());
                if (i.slideCount() != null) n.put("slide_count", i.slideCount());
            }
            ArrayNode u = o.putArray("untracked");
            untracked.forEach(u::add);
            ArrayNode st = o.putArray("account_ticket_ids");
            ticketIds.forEach(st::add);
            ArrayNode sj = o.putArray("account_job_ids");
            jobIds.forEach(sj::add);
            o.put("policy", "ticket target = contract_sla_minutes x {P1:1, P2:2, P3:5}, P4 untracked; job target = "
                    + "contract_sla_minutes; breached > 100%, at_risk >= 75%");
            return o;
        }
    }

    /** The snapshot stored with a proposal - what the human saw, and what apply re-checks the comment against. */
    public static Report fromJson(JsonNode o) {
        List<Item> items = new ArrayList<>();
        for (JsonNode n : o.path("items")) {
            items.add(new Item(n.path("item").asText(), n.path("kind").asText(), n.hasNonNull("priority") ? n.get("priority").asText() : null,
                    n.path("status").asText(), n.hasNonNull("title") ? n.get("title").asText() : null, n.path("started_at").asText(),
                    n.path("elapsed_minutes").asLong(), n.path("target_minutes").asLong(), n.path("pct_of_target").asInt(),
                    n.path("state").asText(), n.hasNonNull("slide_count") ? n.get("slide_count").asInt() : null));
        }
        List<String> untracked = new ArrayList<>();
        o.path("untracked").forEach(x -> untracked.add(x.asText()));
        Set<String> t = new LinkedHashSet<>(), j = new LinkedHashSet<>();
        o.path("account_ticket_ids").forEach(x -> t.add(x.asText()));
        o.path("account_job_ids").forEach(x -> j.add(x.asText()));
        return new Report(o.path("account_id").asText(), o.path("account_name").asText(), o.path("tier").asText(),
                o.path("contract_sla_minutes").asInt(), o.path("as_of").asText(), List.copyOf(items), List.copyOf(untracked), t, j);
    }

    public static OffsetDateTime parseInstant(String s) {
        try {
            return OffsetDateTime.parse(s);
        } catch (DateTimeParseException | NullPointerException e) {
            throw new IllegalArgumentException("as_of must be ISO-8601 with an offset, e.g. 2026-09-24T10:30:00+05:30 (got " + s + ")");
        }
    }

    /** One timestamp format in every language: 2026-09-24T10:30:00+05:30 (seconds always present). */
    public static final java.time.format.DateTimeFormatter ISO = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx");

    public static String iso(OffsetDateTime t) { return t.format(ISO); }

    public static String state(long elapsed, long target) {
        if (elapsed > target) return "breached";
        return elapsed >= AT_RISK * target ? "at_risk" : "ok";
    }

    /** Reads the account, its tickets (each one, for created_at) and its jobs, and applies the policy at asOf. */
    public static Report compute(OpsReader ops, String accountId, OffsetDateTime asOf) {
        JsonNode acc = ops.account(accountId);
        int sla = acc.path("contract_sla_minutes").asInt(0);
        if (sla <= 0) throw new OpsReader.OpsError(502, "invalid", "account " + accountId + " has no contract_sla_minutes");
        List<Item> items = new ArrayList<>();
        List<String> untracked = new ArrayList<>();
        Set<String> ticketIds = new LinkedHashSet<>(), jobIds = new LinkedHashSet<>();

        int read = 0;
        for (JsonNode t : ops.tickets(accountId).path("tickets")) {
            if (!accountId.equals(t.path("account_id").asText(accountId))) continue;     // the tenant boundary, in code too
            String id = t.path("id").asText();
            ticketIds.add(id);
            if (!OPEN_TICKET.contains(t.path("status").asText()) || read >= MAX_TICKETS) continue;
            JsonNode full = ops.ticket(id);
            read++;
            OffsetDateTime created = parseInstant(full.path("created_at").asText());
            if (created.isAfter(asOf)) { ticketIds.remove(id); continue; }                // did not exist yet
            Integer mult = PRIORITY_MULTIPLIER.get(full.path("priority").asText());
            if (mult == null) { untracked.add(id); continue; }
            long target = (long) sla * mult;
            long elapsed = Duration.between(created, asOf).toMinutes();
            items.add(new Item(id, "ticket", full.path("priority").asText(), full.path("status").asText(),
                    Tools.cut(full.path("title").asText(), 120), iso(created), elapsed, target,
                    (int) Math.round(100.0 * elapsed / target), state(elapsed, target), null));
        }
        for (JsonNode j : ops.jobs(accountId).path("jobs")) {
            if (!accountId.equals(j.path("account_id").asText(accountId))) continue;
            String id = j.path("id").asText();
            OffsetDateTime sub = parseInstant(j.path("submitted_at").asText());
            if (sub.isAfter(asOf)) continue;
            jobIds.add(id);
            if (!ACTIVE_JOB.contains(j.path("status").asText())) continue;
            long elapsed = Duration.between(sub, asOf).toMinutes();
            items.add(new Item(id, "job", null, j.path("status").asText(), null, iso(sub), elapsed, sla,
                    (int) Math.round(100.0 * elapsed / sla), state(elapsed, sla), j.path("slide_count").asInt()));
        }
        items.sort(Comparator.comparingInt(Item::pct).reversed().thenComparing(Item::id));
        return new Report(accountId, acc.path("name").asText(), acc.path("tier").asText(), sla, iso(asOf),
                List.copyOf(items), List.copyOf(untracked), ticketIds, jobIds);
    }
}
