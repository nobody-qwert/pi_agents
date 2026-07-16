# 005: Work-plan DAG and leaf-readiness validator

## Objective

Validate untrusted proposed work graphs into approved, dependency-safe work-node
data without executing any work.

## Context and references

- `docs/design/PLAN.md` Sections 3.2-3.3, 4.3, 6, and 7.3.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4.1 and 6.4.

## Dependencies

- 002.

## In scope

- Unique ID/reference, parent, dependency, and acyclic-graph checks.
- Known type/owner/authority, depth/count budget, and protected-artifact checks.
- Interface producer/consumer ordering and acceptance-coverage validation.
- Exact leaf-readiness evaluation with structured rejection reasons.

## Out of scope

- Planning prompts, packet creation, ready-queue persistence, and dispatch.
- Repairing or guessing missing proposal content.

## Implementation constraints

- Validation is deterministic and side-effect free.
- Invalid subgraphs are rejected as a whole unless an explicitly designed
  atomic partial-acceptance contract exists in the baseline.
- Criterion coverage includes integration and final verification nodes.

## Acceptance criteria

- Valid fixture DAGs produce a normalized approved plan.
- Cycles, dangling references, authority violations, uncovered criteria, and
  non-leaf-ready executable nodes fail with stable rule identifiers.
- Property or generated tests cover graph order and cycle edge cases.

## Verification

- Run focused validator tests plus backend quality checks.

## Handoff

- Report policy rule IDs and normalization behavior; stop before persistence.

