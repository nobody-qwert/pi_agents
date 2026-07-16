# 027: Conversation, run, approval, workspace, and promotion commands

## Objective

Expose authenticated command/query endpoints for the complete application
services without executing long-running work in API request handlers.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 6.1 and 9.

## Dependencies

- 007, 009, 012-013, 018, and 020-026.

## In scope

- Conversation/message and run create/get/list/cancel operations.
- Work graph, approvals/decisions, artifacts, and workspace status queries.
- Checkpoint/create/rollback, desktop session/input-owner commands.
- Promotion preview/list/confirm commands and idempotency input handling.

## Out of scope

- SSE implementation, WebSocket byte proxying, business logic duplication, and
  frontend clients.

## Implementation constraints

- Commands enqueue/commit durable intent and return promptly; API workers do not
  run LangGraph or wait for model/tool completion.
- Every mutation enforces ownership, expected version, and idempotency.
- Authentication fields from request bodies are never trusted as actor identity.

## Acceptance criteria

- API integration tests cover happy paths, unauthorized/cross-run access, stale
  versions, replay, conflict, and service failure mapping.
- Run creation cannot begin with an invalid project or unready workspace.
- OpenAPI describes all initial command/query contracts without leaking internal
  storage paths or secrets.

## Verification

- Run command API tests against PostgreSQL plus backend quality checks.

## Handoff

- Report endpoint coverage and async command semantics; stop before streaming.
