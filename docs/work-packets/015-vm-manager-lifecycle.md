# 015: Typed disposable VM lifecycle

## Objective

Create a narrow VM-manager boundary that provisions, probes, and destroys one
run-scoped non-root QEMU/KVM guest without exposing a generic host shell.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.5, 6.7, 11, and 13.
- `docs/design/PLAN.md` Section 3.5.

## Dependencies

- 014.

## In scope

- VM manager package/service skeleton and typed lifecycle operations.
- Run-to-overlay identity, QMP/control-channel readiness, bounded timeouts, and
  idempotent cleanup.
- Immutable base image expectations and non-root guest/host process policy.
- Lifecycle adapter tests using controlled process/VM fixtures where hardware is
  unavailable, plus an explicit KVM integration profile.

## Out of scope

- Workspace transfer, guest tools, desktop proxy, egress, checkpoints, and Docker
  Compose integration.

## Implementation constraints

- No Docker socket, arbitrary command endpoint, host credential forwarding, or
  writable project mount.
- VM identifiers and paths are derived from validated service-owned records.
- Do not claim the KVM integration check passed unless it actually ran.

## Acceptance criteria

- Create/probe/destroy operations are idempotent and auditable.
- Invalid IDs/state and stale operations cannot target another run's guest.
- The KVM profile proves a non-root guest becomes ready and cleanup removes its
  overlay when suitable infrastructure exists.

## Verification

- Run lifecycle contract tests and, when available, the documented KVM profile.

## Handoff

- Report lifecycle states and infrastructure prerequisites; stop before import.

