# 032: Project, guest desktop, checkpoint, and rollback UI

## Objective

Provide the complete safe workspace interaction: select a project, inspect copy
policy, view/control the guest desktop, and manage checkpoint rollback.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 10.2, 10.5-10.6, and 14.3.

## Dependencies

- 020, 027, and 029.

## In scope

- Allowlisted project browser and source/copy-policy/readiness preview.
- Authenticated embedded desktop connection, expiry/reconnect, and status.
- Explicit take/return control with automation-paused acknowledgment.
- Checkpoint list/current lineage, create action, rollback preview/confirm/result.
- Local application preview links supplied by the backend.

## Out of scope

- Promotion, approval decisions, raw VNC configuration, or arbitrary host path
  entry/file transfer/clipboard.

## Implementation constraints

- Destructive-looking rollback requires clear target/effect confirmation, though
  prior authoritative history remains preserved.
- Input-owner state comes from durable backend state/events.
- Tokens and internal guest addresses are not persisted in browser logs/storage.

## Acceptance criteria

- Users cannot select outside server-provided projects.
- Desktop expiry/reconnect and ownership transitions show correct accessible state.
- Checkpoint create/rollback updates current state and lineage without optimistic
  false-success UI.

## Verification

- Run project, desktop, ownership, checkpoint, rollback, and accessibility tests.

## Handoff

- Report user flows and token handling; stop before promotion/approvals.
