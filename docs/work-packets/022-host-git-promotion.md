# 022: Confirmed isolated host Git promotion

## Objective

Apply one authenticated immutable preview to an isolated host worktree, validate
it, and create a new branch/commit plus optional unique tag without changing the
user's current checkout.

## Context and references

- `docs/design/PLAN.md` Sections 3.5 and 7.7.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.8, 6.9, and 13.

## Dependencies

- 003, 006-008, and 021.

## In scope

- Confirmation nonce/authority/idempotency and exact version/message/tag input.
- Final source HEAD/cleanliness and preview/check revalidation.
- Isolated worktree/branch creation, manifest application, required checks,
  commit, optional annotated tag, cleanup, durable record/events.
- Separate versioned review repository fallback for ineligible sources.

## Out of scope

- Merge/push, changing the current branch/worktree, arbitrary Git commands, and
  frontend confirmation.

## Implementation constraints

- Git operations are service-authored, not model-supplied.
- Dirty, advanced, conflicting, non-Git, duplicate-version, or failed-check
  sources never receive direct mutation.
- Partial failure is recoverable and never claims promotion success.

## Acceptance criteria

- Clean eligible fixture produces the expected branch/commit/tag while current
  checkout HEAD and files remain byte-identical.
- Every refusal/fallback case leaves the source untouched with an auditable
  result.
- Replaying the same confirmation is safe; conflicting reuse is rejected.

## Verification

- Run comprehensive Git fixture integration tests and compare current checkout
  before/after each case.

## Handoff

- Report branch/tag/fallback semantics; stop before API and UI exposure.

