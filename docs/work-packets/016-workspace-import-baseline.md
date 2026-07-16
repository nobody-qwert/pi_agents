# 016: Sanitized workspace import and guest Git baseline

## Objective

Copy an approved project snapshot into its run-scoped guest workspace, verify the
manifest, and initialize a separate immutable guest Git baseline.

## Context and references

- `docs/design/PLAN.md` Section 3.5.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.5, 3.8, and 6.7.

## Dependencies

- 014-015.

## In scope

- Typed copy-in operation from the trusted read-only source boundary.
- Manifest/hash verification and race/fingerprint recheck.
- Guest path ownership/permissions and separate Git initialization/baseline.
- Durable workspace session and transfer metadata/events.

## Out of scope

- Model tools, accepted checkpoints, rollback, copy-out, and host promotion.

## Implementation constraints

- Source `.git` and protected/excluded paths never enter the guest.
- The guest never mounts or learns a usable writable host source path.
- Partial or changed-source imports fail and clean up without an authoritative
  ready workspace.

## Acceptance criteria

- Clean and dirty fixture snapshots import according to the recorded policy.
- Excluded content is absent; included hashes match the durable manifest.
- Guest Git has one service-owned baseline and no host repository metadata.
- A guest mutation cannot affect the source fixture.

## Verification

- Run transfer integration tests, including source-race and exclusion cases.

## Handoff

- Report manifest/baseline formats; stop before tools or checkpoints.

