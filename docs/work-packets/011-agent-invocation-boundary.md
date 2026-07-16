# 011: Validated agent invocation boundary

## Objective

Invoke a registry-pinned agent through the model gateway and return only a
schema- and policy-validated proposal/report or an explicit rejection.

## Context and references

- `docs/design/PLAN.md` Sections 3.4, 5, 6, and 8.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.4, 5, and 8.2.

## Dependencies

- 004, 008, and 010.

## In scope

- Prompt/config snapshot resolution and attempt identity.
- Typed context assembly from referenced artifacts with bounded size.
- Model invocation, strict result parsing, policy validation, and attempt report.
- Durable large-input/output references and safe validation event details.

## Out of scope

- Stage-specific planning logic, guest tools, dispatch, repair, or accepting a
  proposal into authoritative state.

## Implementation constraints

- Free-form text never supplies a command, transition, path, URL, or authority.
- Pin registry, prompt, model, design, packet, and attempt versions.
- Malformed output is visible and bounded; it is not repaired by permissive
  parsing.

## Acceptance criteria

- Valid fixtures produce typed untrusted results with complete provenance.
- Unknown fields, wrong result type, oversize context, stale references, and
  authority violations are rejected and recorded safely.
- Retry policy cannot turn invalid content into an unbounded loop.

## Verification

- Run invocation/validation unit tests and an opt-in LM Studio contract check.

## Handoff

- Report accepted result types and rejection taxonomy; stop before authoritative
  acceptance or dispatch.

