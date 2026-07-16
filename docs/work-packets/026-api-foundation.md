# 026: FastAPI foundation and read-only system queries

## Objective

Create the FastAPI service boundary with health/readiness and safe read-only
queries for the fixed graph, agent registry, and allowlisted project catalog.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 6.1, 9, and 11.1.

## Dependencies

- 004, 010, and 014.

## In scope

- Application factory, versioned routing, error envelope, request IDs, and safe
  development identity boundary using the recommended initial design choice.
- `/health` and dependency-aware `/ready` including configured LM Studio status.
- System graph/agent and project list/detail endpoints.
- Authorization/service mapping tests and generated OpenAPI checks.

## Out of scope

- Run commands, events/SSE, artifact content, workspace mutation, and frontend.

## Implementation constraints

- Route handlers validate/authorize and call application services; no domain or
  repository policy lives in handlers.
- Registry projections omit secrets and protected policy.
- Project endpoints accept/return opaque IDs, never client-controlled paths.

## Acceptance criteria

- Health remains useful while readiness reports precise unavailable dependencies.
- Read-only endpoints match typed projections and enforce identity/authorization.
- Invalid identifiers and service failures map to stable safe errors.

## Verification

- Run API contract/OpenAPI tests and backend quality checks.

## Handoff

- Report identity convention and route/error contracts; stop before commands.
