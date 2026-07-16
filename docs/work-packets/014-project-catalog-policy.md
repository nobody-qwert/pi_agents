# 014: Allowlisted project catalog and copy policy

## Objective

Discover selectable projects under administrator-configured roots and produce a
safe, opaque selection plus sanitized-copy preview.

## Context and references

- `docs/design/PLAN.md` Section 3.5.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.5, 9, and 13.

## Dependencies

- 002.

## In scope

- Canonical root/project resolution and opaque project IDs.
- Git HEAD/cleanliness and working-tree fingerprint inspection.
- Default-deny/exclusion manifest for secrets, `.git`, caches, and build output.
- Size/count estimation and protected-path policy projection.

## Out of scope

- Copying files, creating VMs, HTTP routes, or promotion.

## Implementation constraints

- Clients/models never submit an arbitrary absolute source path.
- Symlinks, traversal, nested roots, races, and unreadable paths fail safely.
- Inspection is read-only and does not alter the selected repository.

## Acceptance criteria

- Only valid descendants of configured roots are discoverable/selectable.
- Adversarial traversal and symlink fixtures cannot escape roots.
- Preview reports fingerprint, Git eligibility, exclusions, protected paths, and
  bounded size without exposing excluded content.

## Verification

- Run policy tests against clean, dirty, non-Git, symlink, and traversal fixtures.

## Handoff

- Report ID/fingerprint and exclusion algorithms; stop before transfer.

