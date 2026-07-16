# 036: Recovery, adversarial, accessibility, and pilot end-to-end hardening

## Objective

Prove the first software-domain vertical slice satisfies the full milestone under
normal, interrupted, malformed, and hostile conditions using the required model.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 13-16.
- `docs/design/PLAN.md` Sections 10, 11 Phase 4, and 14.

## Dependencies

- 035.

## In scope

- The complete LM Studio `qwen3.6-27b` end-to-end scenario in Section 14.4.
- Crash/restart, duplicate delivery, stale approval, rollback/resume, and
  promotion conflict/replay suites.
- Prompt/tool/path/egress/desktop/artifact adversarial tests and budget exhaustion.
- Frontend accessibility/keyboard/reconnect audit and first bounded software pilot.
- Documentation corrections based on verified behavior.

## Out of scope

- New product features, a second domain profile, production multi-tenancy, or
  weakening assertions to make the pilot pass.

## Implementation constraints

- Use real LM Studio and the configured guest runtime for runnable/E2E flows.
- Keep deterministic assertions around state/evidence; do not assert exact model
  prose.
- A missing infrastructure prerequisite is a concrete blocker, not a skipped-pass.

## Acceptance criteria

- Every first-milestone criterion has linked automated or explicitly recorded
  manual evidence.
- Injected failures cannot bypass validation, isolation, approval, verification,
  or host-promotion protections.
- Clean restart/replay is durable, UI flows are keyboard accessible, and the
  original source checkout remains unchanged.

## Verification

- Run all repository acceptance commands and the complete documented E2E profile;
  preserve concise failure fingerprints for any blocker.

## Handoff

- Produce a criterion-by-criterion evidence report. Do not start Phase 5 or add a
  second domain profile in this packet.
