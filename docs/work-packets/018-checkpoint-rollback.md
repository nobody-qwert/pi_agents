# 018: Guest checkpoint and rollback service

## Objective

Create durable, lineage-preserving guest Git checkpoints and restore a selected
checkpoint idempotently without rewriting prior evidence.

## Context and references

- `docs/design/PLAN.md` Sections 3.5 and 7.7.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.8 and 6.8.

## Dependencies

- 006-007 and 016.

## In scope

- Baseline, service-accepted, user-accepted, and rollback checkpoint semantics.
- Commit/tree verification, metadata/evidence/design links, and lineage records.
- Mutation pause/guard during checkpoint or rollback.
- List and restore application service operations with events/idempotency.

## Out of scope

- Deciding work verification, automatic commit after every attempt, API/UI, and
  host promotion.

## Implementation constraints

- Git is recovery state; database records remain authoritative.
- Rollback adds lineage/state events and never deletes old checkpoints/evidence.
- `USER_ACCEPTED` does not imply independently verified.

## Acceptance criteria

- Valid checkpoint creation records exact commit/tree and linked provenance.
- Restore returns the guest tree to the chosen state and can safely replay.
- Foreign, damaged, stale, or concurrent checkpoint requests are rejected.

## Verification

- Run Git fixture integration tests for lineage, restore, corruption, and replay.

## Handoff

- Report checkpoint types and pause protocol; stop before promotion.

