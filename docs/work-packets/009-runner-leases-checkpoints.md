# 009: Runner leases and LangGraph checkpoint recovery

## Objective

Create a runner control shell that exclusively leases a run, advances only the
fixed graph, and resumes safely from durable LangGraph checkpoints.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.1, 4, and 6.2.
- `docs/design/PLAN.md` Sections 3.4 and 7.

## Dependencies

- 003 and 006-007.

## In scope

- PostgreSQL-backed run queue/lease with expiry and compare-and-swap renewal.
- LangGraph PostgreSQL checkpointer integration for the fixed graph.
- Coordinator shell mapping validated statuses through transition service.
- Crash/resume, duplicate delivery, bounded attempt, and cancellation behavior.

## Out of scope

- Real model invocation, tool execution, full domain stage logic, and API worker
  management.

## Implementation constraints

- Only one current lease holder may advance a run.
- Domain records remain authoritative; LangGraph checkpoints are recovery state.
- Tests may stub application stage ports but must not add a fake-model runtime
  mode.

## Acceptance criteria

- A deterministic no-model fixture traverses only permitted gates.
- Killing/restarting the runner resumes without duplicating committed transitions
  or events.
- Lost leases, stale checkpoints, cancellation, and exhausted budgets produce
  explicit safe states.

## Verification

- Run runner/checkpoint integration tests including forced interruption.

## Handoff

- Report lease timing and resume invariants; stop before model wiring.

