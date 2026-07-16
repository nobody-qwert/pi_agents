# 006: PostgreSQL migrations and repository boundaries

## Objective

Persist authoritative records behind small repository interfaces with optimistic
concurrency and transaction support.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.1, 6, and 7.
- `docs/design/PLAN.md` Section 3.4 authoritative write boundaries.

## Dependencies

- 002-003.

## In scope

- Initial migrations for the tables in the durable data model.
- Repository ports and PostgreSQL adapters organized by aggregate ownership.
- Record-version compare-and-swap, transaction unit, and test database fixture.
- Persistence mapping tests for authoritative schema types.

## Out of scope

- Event tailing, runner leases, LangGraph checkpoints, API routes, and artifact
  file content.
- Business policy inside repositories.

## Implementation constraints

- Application/domain layers do not depend on ORM-specific types.
- Database constraints reinforce unique IDs, sequence/version rules, and foreign
  references; they do not replace domain validation.
- Migrations and lockfiles are changed only where this packet explicitly owns
  them.

## Acceptance criteria

- Migrations apply to an empty database and repository integration tests pass.
- Stale writes and duplicate authoritative identifiers are rejected.
- Stored records round-trip without losing type/version/audit data.

## Verification

- Run migration and repository integration tests against PostgreSQL, then
  backend quality checks.

## Handoff

- Report migration head and repository transaction contract; stop before events.

