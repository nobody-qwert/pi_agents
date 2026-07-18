# Lean Task Packet

This template defines the design-anchored handoff for one implementation task.
It must agree with the applicable package's `implementation-plan.md` entry.

A packet is runnable only when the design is reviewed and ready, every stable
requirement reference resolves, and every semantic design content fingerprint
matches the verifier-authored `REVIEWED_FINGERPRINTS` in `status.md`. Fingerprints
may be computed with `git hash-object`, including for uncommitted files; they are
document-version checks, not Git workspace snapshots.

Coding workers never edit `docs/design/**`. Expected paths are informed starting
points, not an exhaustive allowlist.

```text
TASK_ID: <stable short id>

DESIGN_ID: <stable design package id>

DESIGN_REVISION: <reviewed positive integer>

DESIGN_REFS:
- <docs/design path>::<stable requirement id>

DESIGN_FINGERPRINTS:
- docs/design/<design-id>/index.md: <content fingerprint>
- docs/design/<design-id>/implementation-plan.md: <content fingerprint>
- <referenced high-level or module design path>: <content fingerprint>

GOAL:
<one observable outcome>

ACCEPTANCE_CRITERIA:
- <behavior that can be checked>

EXPECTED_PATHS:
- <informed starting path>

ENTRY_SYMBOLS:
- <verified symbol or starting file>

DEPENDS_ON:
- <task id whose verified outcome is required first, or none>

ACCEPTANCE_COMMANDS:
- <exact bounded command>

CONSTRAINTS:
- <public behavior or boundary that must remain unchanged>

KNOWN_FACTS:
- <fact verified from repository evidence>

KNOWN_FAILED_APPROACHES:
- <short fingerprint only, or none>
```

The worker stops when the outcome must materially broaden or the approved design
cannot support the implementation. The harness does not attach a workspace
inventory or protected-path manifest and does not claim exhaustive edit
attribution.
