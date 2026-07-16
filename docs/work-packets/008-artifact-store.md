# 008: Versioned artifact storage boundary

## Objective

Provide the sole authoritative interface for validated artifact content and
metadata, backed by the local-volume adapter for the first milestone.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.1, 6.6, 7, and 13.
- `docs/design/PLAN.md` Sections 3.4 and 4.

## Dependencies

- 002 and 006.

## In scope

- Artifact service/port, local-volume adapter, metadata repository integration.
- Logical ID, version, hash, size/type, scope, and expected-version policy.
- Atomic publish/read behavior and safe metadata/preview projection.
- Tests for conflicts, path escape, content limits, and access decisions.

## Out of scope

- Guest copy-in/out, object-store adapters, HTTP download routes, and UI previews.
- Treating provisional guest workspace mutations as authoritative artifacts.

## Implementation constraints

- Storage paths are derived internally from validated IDs, never supplied raw by
  a model/client.
- Content hashes and metadata commits cannot claim content that was not stored.
- Keep policy separate from the filesystem adapter.

## Acceptance criteria

- Valid artifacts publish and read by immutable version and hash.
- Stale writes, traversal, oversized/disallowed content, and cross-scope access
  are rejected without partial authoritative records.
- Adapter contract tests can be reused for a future object-store implementation.

## Verification

- Run artifact unit/integration tests and backend quality checks.

## Handoff

- Report artifact port semantics and configured local limits; stop before VM
  transfers.

