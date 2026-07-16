# 002: Strict domain schema primitives

## Objective

Define the versioned, strict schema vocabulary used at authoritative and agent
boundaries without adding persistence or workflow behavior.

## Context and references

- `docs/design/PLAN.md` Sections 3.2-3.4, 4, and 6.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4, 7, and 8.

## Dependencies

- 001.

## In scope

- Pydantic v2 base model policy that rejects unknown fields.
- Stable typed identifiers, versions, timestamps, statuses, actor/authority
  types, and common metadata.
- Records and proposal/report schemas named in the authoritative data model.
- Serialization fixtures covering valid and invalid examples.

## Out of scope

- Transition decisions, graph algorithms, ORM mappings, and API DTOs tailored to
  individual routes.
- Schema migrations for hypothetical future versions.

## Implementation constraints

- Separate authoritative records from untrusted proposals/reports by type.
- Avoid one giant state model; group schemas by clear domain responsibility.
- Agent-provided fields can never imply authentication or authoritative state.

## Acceptance criteria

- Unknown fields, invalid IDs/statuses, and malformed version data are rejected.
- Round-trip serialization is deterministic for accepted fixtures.
- Tests demonstrate that proposal types cannot be substituted for authoritative
  approval, evidence, transition, or completion records.

## Verification

- Run schema unit tests plus backend lint and type checks.

## Handoff

- List exported schema modules and any unresolved vocabulary question; stop
  before adding state transitions.

