# 023: Intake, design, critique, and planning stages

## Objective

Implement the fixed graph's pre-delivery stages so validated agent proposals can
be accepted into authoritative charter, design, and approved work-plan records.

## Context and references

- `docs/design/PLAN.md` Sections 4.1-4.4, 5, and 7.1-7.3.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4-5.

## Dependencies

- 009-013.

## In scope

- Stage application services for intake, investigation, design, design critique,
  planning, and plan validation.
- Role-specific context assembly and validated result handling through packet 011.
- Deterministic acceptance into charter/design/work-node records and events.
- Bounded revision, authority-question, rejection, and blocker routing.

## Out of scope

- Worker/tool execution, local verification, integration, outcome verification,
  and frontend/API transport.

## Implementation constraints

- Agents propose; deterministic services accept/reject and transition.
- Design critic is independent from design authorship and cannot rewrite/approve
  by assertion.
- Planning creates dynamic work data only and cannot alter control topology.

## Acceptance criteria

- A valid fixture request reaches an accepted design and approved leaf-ready work
  plan with full criterion/interface traceability.
- Malformed proposals, uncovered criteria, bad authority, and revision exhaustion
  route visibly without authoritative partial acceptance.
- Restart/retry does not duplicate revisions, nodes, packets, or events.

## Verification

- Run stage service tests, a no-tool workflow integration test, and backend checks.

## Handoff

- Report stage inputs/results and acceptance owners; stop before delivery stages.

