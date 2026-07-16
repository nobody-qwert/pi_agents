# 003: Deterministic transition and idempotency policy

## Objective

Implement the pure domain service that is the only authority for allowed run and
work-node state transitions.

## Context and references

- `docs/design/PLAN.md` Sections 3.2-3.4 and 4.3.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4 and 6.3.

## Dependencies

- 002.

## In scope

- Explicit transition tables for run gates and work-node states.
- Pure guards accepting current typed state plus validated decision input.
- Optimistic version and stable idempotency-key rules.
- Structured accepted/rejected transition results and audit payloads.

## Out of scope

- Database transactions, LangGraph routing, approvals, retries, and API commands.
- Any model involvement in selecting a transition.

## Implementation constraints

- Default deny every unlisted edge.
- Replaying the same accepted command is safe; conflicting reuse is rejected.
- A worker/model completion claim cannot directly enter a verified or complete
  state.

## Acceptance criteria

- Tests cover every permitted edge and representative forbidden edges.
- Stale versions, invalid actors, and conflicting idempotency keys are rejected.
- Table-completeness tests fail if an enum state is left unconsidered.

## Verification

- Run focused transition tests and all backend quality checks.

## Handoff

- Report the resulting transition matrices and stop before persistence or graph
  compilation.

