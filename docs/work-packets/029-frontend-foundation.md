# 029: Typed React application foundation

## Objective

Create the React/TypeScript/Vite application shell, typed API boundaries, routing,
and reusable accessible inspector/layout primitives.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.2, 10.1, and 12.

## Dependencies

- 026-028.

## In scope

- Frontend package, lint/type/test/build commands, and Dockerfile skeleton.
- Four-view shell/navigation and shared right-side inspector structure.
- Typed HTTP query/command client, error mapping, and TanStack Query setup.
- Sanitized Markdown component and baseline accessibility/testing utilities.

## Out of scope

- Graph canvas, chat timeline, SSE behavior, desktop embedding, and promotion
  screens.

## Implementation constraints

- Generate or maintain API types from an explicit contract; do not scatter
  unchecked response casts.
- HTML is disabled or strictly sanitized.
- Components start small and feature-local; no premature global design system.

## Acceptance criteria

- Shell routes render accessible loading/error/empty states using typed fixtures.
- Unsafe Markdown fixtures cannot inject script/HTML behavior.
- Frontend test, type-check, lint, and production build commands pass.

## Verification

- Run all frontend commands introduced in this packet.

## Handoff

- Report commands, API type strategy, and shared primitives; stop before features.
