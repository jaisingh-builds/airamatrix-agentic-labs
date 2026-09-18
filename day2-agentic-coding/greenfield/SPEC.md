# Lab 2 — Greenfield: spec to running service in 60 minutes

You are building a small service, agent-driven, from this specification. Nothing
is scaffolded for you on purpose — scoping the work so the agent can finish it is
half the exercise.

## Estimate first

**Before you start, write down in `../metrics/baseline-estimate.md` how long you
think this would take you by hand.** Do it now. An estimate written afterwards is
worthless, and the comparison is the only honest measure of what you gained.

---

## Slide Intake Service

A small HTTP service that accepts slide-analysis job submissions, validates them,
and reports status.

### Endpoints

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/jobs` | Submit a job. Returns `201` with the created job. |
| `GET` | `/jobs/{id}` | Return the job, or `404`. |
| `GET` | `/jobs?status=queued` | List jobs, optionally filtered by status. |
| `POST` | `/jobs/{id}/cancel` | Cancel a queued job. `409` if it is already `completed`. |

### The job

```json
{
  "id": "generated, opaque",
  "accountId": "ACC-1001",
  "slideCount": 250,
  "priority": "normal",
  "status": "queued",
  "submittedAt": "2026-09-19T09:30:00+05:30"
}
```

### Validation rules

These are the interesting part. Be precise with the agent about them.

1. `accountId` must match `ACC-` followed by exactly four digits.
2. `slideCount` must be between 1 and 5000 inclusive.
3. `priority` must be one of `low`, `normal`, `rush`.
4. A `rush` job with `slideCount` above 1000 is rejected — rush capacity is
   limited. Error message must say so specifically.
5. Rejections return `400` with a body naming **every** failed field, not just
   the first.

### Status transitions

`queued -> running -> completed`, and `queued -> cancelled`.
Any other transition is a `409`.

### Non-functional

- Tests for every validation rule and every rejected transition.
- No database. In-memory is fine.
- Java/Spring Boot or TypeScript/Node — your team's stack.

---

## How to work

This is the **plan → act → diff → review** loop:

1. **Plan.** Ask for a plan first. Read it. Push back on it. A plan you did not
   read is not a plan.
2. **Act in slices.** One intent per change. "Add the model and its validation"
   is a slice. "Build the service" is not.
3. **Diff.** After every slice, read the whole diff. Every line.
4. **Review.** Use `/review-diff`.

## Done when

- [ ] The service runs
- [ ] Every validation rule has a test, including rule 4
- [ ] Every rejected transition has a test
- [ ] You compared against your written estimate
- [ ] Every diff was reviewed before the next one started

## What to watch for

- **Rule 4 and rule 5 are where agents cut corners.** Check them specifically.
- **Tests that assert the happy path only.** Ask what happens with `slideCount: 0`,
  `5001`, `-1`, missing, and a string.
- **Scope creep.** If it starts adding authentication or a database, it has
  stopped doing what you asked.
