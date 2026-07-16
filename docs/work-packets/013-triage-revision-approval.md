# 013: Issue triage, design revision impact, and approval gates

## Objective

Route validated issues to the smallest permitted loop, invalidate precisely
affected work after design changes, and pause/resume on authenticated decisions.

## Context and references

- `docs/design/PLAN.md` Sections 4.4-4.5 and 7.5-7.6.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4 and 7.

## Dependencies

- 003, 005-008.

## In scope

- Issue acceptance/classification and deterministic route selection.
- Design/interface reference impact calculation and affected-node invalidation.
- Immutable revision lineage and selective subgraph revalidation.
- Approval request/decision service with actor authority, expiry, versions, and
  idempotency.

## Out of scope

- Agents that author design/triage prose, repair work, API routes, and UI forms.

## Implementation constraints

- An agent may propose classification but cannot accept its own route or design.
- Unaffected verified nodes/evidence remain intact.
- Stale, unauthorized, expired, or replay-conflicting approvals are rejected.

## Acceptance criteria

- Every issue class maps to only permitted destinations.
- Design revision tests invalidate exactly nodes referencing changed contracts
  plus dependent consumers requiring revalidation.
- Approval pause/resume is atomic, audited, and cannot skip a gate.

## Verification

- Run issue-routing, impact-analysis, and approval concurrency tests.

## Handoff

- Report route and invalidation rules; stop before transport or UI.

