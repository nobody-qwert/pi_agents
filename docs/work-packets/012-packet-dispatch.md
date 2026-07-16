# 012: Immutable packet issuance and ready dispatch

## Objective

Turn approved leaf nodes into minimal immutable packets and dispatch only nodes
whose verified dependencies and authority constraints are satisfied.

## Context and references

- `docs/design/PLAN.md` Sections 3.3, 4.3, 6, 7.3-7.4, and 8.
- `docs/design/TECHNICAL_DETAILS.md` Sections 4.1 and 6.4.

## Dependencies

- 005-008.

## In scope

- Deterministic ready-frontier selection.
- Packet assembly from canonical references and immutable packet persistence.
- Dependency/design/input version pinning and stale-packet detection.
- Claim/attempt issuance with per-node retry and budget enforcement.

## Out of scope

- Planner prompts, worker execution, local verification, triage, and UI queues.

## Implementation constraints

- Packets follow `docs/design/PLAN.md` Section 6 and omit unrelated history/content.
- Dispatch cannot approve a proposed node or bypass unmet dependencies.
- Repeated issuance for the same versions is idempotent.

## Acceptance criteria

- Only approved leaf-ready nodes appear in a stable ready order.
- Issued packets contain every required contract field and no secret or unrelated
  branch context.
- Changed dependencies/design invalidate or supersede stale packets explicitly.

## Verification

- Run ready-queue, packet-content, idempotency, and stale-version tests.

## Handoff

- Report packet version/claim semantics; stop before invoking workers.

