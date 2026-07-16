# 017: Role-scoped guest tool runtime

## Objective

Execute typed Pi-compatible filesystem and shell operations inside the guest
under per-role capability and workspace policies.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.5-3.6, 6.6-6.7, and 13.
- `docs/design/PLAN.md` Sections 3.4-3.5.

## Dependencies

- 011 and 015-016.

## In scope

- `GuestAgentRuntime` port and first Pi SDK/RPC adapter.
- Typed `read`, `write`, `edit`, `bash`, `grep`, `find`, and `ls` requests/results.
- Role allowlists, root/path/command budgets, timeout/cancellation, and audit
  events.
- Read-only verifier versus scoped implementer enforcement.

## Out of scope

- Browser tools, public egress, host execution, workflow dispatch, and artifact
  acceptance.

## Implementation constraints

- Model text cannot become an operation until parsed and policy validated.
- All paths remain beneath the run's guest workspace; no sudo, Docker socket,
  host SSH, or management endpoints.
- Runnable integration uses the configured Pi runtime, not a fake runtime mode.

## Acceptance criteria

- Allowed operations work in the disposable fixture guest and emit bounded audit
  details.
- Escape paths, forbidden tools/roles, oversized output, timeout, and cancellation
  fail safely.
- A verifier cannot mutate and no tool can change the host source fixture.

## Verification

- Run policy/adapter tests and the documented guest integration profile.

## Handoff

- Report tool contracts and role matrix; stop before browser support.

