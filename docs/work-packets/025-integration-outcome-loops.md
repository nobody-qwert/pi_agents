# 025: Integration, outcome verification, and controlled feedback loops

## Objective

Complete the fixed runtime by integrating locally verified outputs, independently
checking every outcome criterion, and routing failures through controlled repair,
redesign, approval, blocked, or completion paths.

## Context and references

- `docs/design/PLAN.md` Sections 7.5-7.6 and 10.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4 and 16.

## Dependencies

- 013, 018, and 023-024.

## In scope

- `INTEGRATE`, `OUTCOME_VERIFY`, `TRIAGE`, approval-resume, and completion stage
  application services.
- Integrator and outcome-verifier context/result handling with independence.
- Criterion/evidence aggregation, blocking-issue and mandatory-approval gates.
- Local repair redispatch, design-revision invalidation/replanning, bounded loop
  budgets, and deterministic terminal-state selection.

## Out of scope

- API/UI, promotion, changing the fixed graph, and second-domain profiles.

## Implementation constraints

- Local verification is never treated as integration or outcome verification.
- Triage proposals are accepted only through packet 013 deterministic policy.
- Completion requires every criterion, integration node, approval, and blocker
  condition to be authoritatively satisfied.

## Acceptance criteria

- Happy-path fixtures reach completion with criterion-by-criterion evidence.
- Injected local defect, interface mismatch, design gap, authority gap, evidence
  gap, and environment blocker each route to the correct loop/state.
- Design revision invalidates/replans only impacted work, and retry/budget
  exhaustion cannot invent completion.

## Verification

- Run full fixed-graph integration tests including every feedback route and resume.

## Handoff

- Report terminal gates and loop budgets; stop before transport.

