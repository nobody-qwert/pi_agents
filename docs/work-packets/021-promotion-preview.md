# 021: Immutable promotion preview and version proposal

## Objective

Build a read-only, immutable preview of the delta from sanitized baseline to a
selected guest checkpoint, including checks and a user-editable version proposal.

## Context and references

- `docs/design/PLAN.md` Sections 3.5 and 7.7.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.8, 6.9, and 10.6.

## Dependencies

- 008, 016, and 018.

## In scope

- Mutation pause and selected-checkpoint freeze.
- Manifest-checked export/diff with protected exclusion enforcement.
- Required-check results, unresolved issues, source baseline comparison, and
  immutable preview identity/hash.
- Next-minor semantic-version proposal and documented fallback-label proposal.

## Out of scope

- Applying files to host Git, authenticated confirmation, API/UI, and tagging.

## Implementation constraints

- Preview never interprets excluded source paths as deletions.
- Version is a proposal only; the user owns the exact confirmed value.
- Changed source/checkpoint after preview cannot reuse that preview silently.

## Acceptance criteria

- Preview contains full diff/manifest/check evidence and destination eligibility.
- Protected path changes, invalid versions, changed baseline, or failed required
  checks disable direct eligibility with explicit reasons.
- Identical inputs replay to the same immutable preview; changed inputs do not.

## Verification

- Run export, protected-path, version, baseline, and idempotency fixture tests.

## Handoff

- Report preview identity and eligibility rules; stop before host mutation.

