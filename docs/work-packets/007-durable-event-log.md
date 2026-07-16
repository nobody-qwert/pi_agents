# 007: Atomic durable event log

## Objective

Write ordered domain events atomically with authoritative state changes and make
them replayable by run sequence.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 6.5 and 8.
- `docs/design/PLAN.md` Sections 3.4 and 8.1.

## Dependencies

- 006.

## In scope

- Event envelope serialization, redacted inline details, and detail references.
- Per-run monotonic sequence allocation inside the state-change transaction.
- Replay queries and PostgreSQL notification wakeups with polling recovery.
- Idempotent event insertion linked to the originating command/transition.

## Out of scope

- SSE transport, UI formatting, OpenTelemetry export, and large artifact storage.

## Implementation constraints

- Publish/notify only after commit; consumers must tolerate missed notifications.
- Event content is an audit projection, not authoritative mutable state.
- Never persist raw chain-of-thought or unrestricted secret-bearing payloads.

## Acceptance criteria

- State plus event commit together or roll back together.
- Concurrent writers yield unique, gap-tolerant monotonic run sequences.
- Replay after a sequence is ordered, stable, authorized at the service boundary,
  and free of duplicates.

## Verification

- Run concurrency, rollback, serialization, and replay integration tests.

## Handoff

- Report the transaction and wakeup contracts; stop before HTTP streaming.

