# 004: Fixed control graph and agent registry

## Objective

Declare the fixed LangGraph topology and load a validated, immutable agent/prompt
registry that can safely be projected to operators.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 4, 5, and 12.
- `docs/design/PLAN.md` Sections 3.4 and 5.

## Dependencies

- 002-003.

## In scope

- Code-defined graph nodes, edges, and conditional status mapping.
- Versioned YAML agent configurations and separately versioned prompt files.
- Startup registry validation, prompt/config hashing, and immutable versioning.
- Redacted registry and graph projection types.

## Out of scope

- Executing the graph, calling a model, database storage, and frontend rendering.
- Runtime graph mutation or configuration editing.

## Implementation constraints

- The UI projection is derived from the same registry used by transition tests.
- Reject duplicate IDs, unknown tools/schemas/authority, bad prompt references,
  and secret-bearing projections.
- Agent authority must never exceed its role in `docs/design/PLAN.md` Section 5.

## Acceptance criteria

- Registry fixtures load deterministically and yield stable hashes.
- Invalid configuration variants fail with focused diagnostics.
- Tests prove the compiled permitted edges match the transition policy and no
  agent/config output can introduce topology.

## Verification

- Run graph/registry unit tests and backend quality checks.

## Handoff

- Record registry locations and projection fields; stop before execution.

