# 028: Replayable SSE and typed event details

## Objective

Stream durable run events with replay/reconnect semantics and serve authorized,
typed detail payloads lazily.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 2, 8, 8.1-8.2, and 9.

## Dependencies

- 007 and 026-027.

## In scope

- `GET /runs/{id}/events` SSE replay then live tail.
- `Last-Event-ID` validation, heartbeat, disconnect, polling recovery, and
  terminal close behavior.
- Authorized typed event-detail endpoint with artifact references for large data.
- Backpressure/connection limits and duplicate-free serialization tests.

## Out of scope

- Desktop WebSockets, frontend SSE client, OpenTelemetry traces, and raw database
  change feeds.

## Implementation constraints

- Durable database sequence is the cursor/source of truth.
- Reauthorization occurs on every connection/reconnect.
- Do not hold a database transaction open for the lifetime of a stream.

## Acceptance criteria

- Client receives ordered replay followed by live events without a gap/duplicate
  across the handoff race.
- Reconnect after an exact sequence resumes correctly; invalid/future cursors and
  cross-run access fail safely.
- Large/sensitive details are lazy, typed, redacted, and separately authorized.

## Verification

- Run async integration tests for replay/live race, reconnect, heartbeat,
  disconnect, terminal state, and authorization.

## Handoff

- Report cursor and terminal semantics; stop before frontend consumption.
