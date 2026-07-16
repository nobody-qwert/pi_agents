# 033: Promotion and human-approval UI

## Objective

Present blocked authority decisions and immutable promotion evidence clearly, and
submit explicit authenticated decisions without hiding conflicts or failed gates.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 10.6 and 14.3.
- `docs/design/PLAN.md` Sections 7.6-7.7.

## Dependencies

- 021-022, 027, and 029.

## In scope

- Pending approval list/detail with affected versions, authority, expiry, and
  approve/reject comment flow.
- Promotion-preview creation and full diff/manifest/check/issue/baseline display.
- Editable exact version, commit message, tag choice, confirmation nonce, and
  conflict-disabled submission.
- Success/fallback/refusal result with branch/commit/tag/review location.

## Out of scope

- Approval/promotion policy, Git operations, diff generation, merge/push, and
  configuration editing.

## Implementation constraints

- UI never turns a model verdict into human approval.
- Confirmation names the exact immutable preview and current eligibility state.
- Failed/stale/conflicting checks remain visible and cannot be bypassed client-side.

## Acceptance criteria

- Approval tests cover accept, reject, stale, expired, unauthorized, and replay.
- Promotion tests cover eligible success and every backend refusal/fallback state.
- Current checkout safety and user-owned version choice are explicit in the flow.

## Verification

- Run promotion/approval component, integration, and accessibility tests.

## Handoff

- Report decision/confirmation UX and stop before observability.
