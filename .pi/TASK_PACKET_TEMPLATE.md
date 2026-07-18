# Lean Task Packet

This template defines the handoff contract for delegated tasks.

Every packet must follow `.pi/DESIGN_PACKAGE_TEMPLATE.md` and agree with its
task entry in the applicable package's `implementation-plan.md` under
`docs/design/<design-id>/`. A packet is runnable only when the design is reviewed
and ready, every reference resolves, and every recorded blob ID still matches.
Each packet fingerprint must equal the corresponding entry in the design
verifier's complete `REVIEWED_FINGERPRINTS` manifest in `status.md`. Otherwise return
`NEEDS_DESIGN_CHANGE` before implementation.
The complete design package is included under `PROTECTED_PATHS` for coding;
only the design workflow may change semantic design files and only the status
writer may change its ledger.
`COMMAND_ARTIFACTS` is copied exactly from the reviewed plan before execution.
It may contain only bounded repository-relative paths outside protected paths and
`docs/design/**`, and authorizes command residue rather than worker edits. No
later report can expand it.

```text
TASK_ID: <stable short id>

DESIGN_ID: <stable design package id>

DESIGN_REVISION: <reviewed positive integer>

DESIGN_REFS:
- <docs/design path>::<stable requirement id>

DESIGN_FINGERPRINTS:
- docs/design/<design-id>/index.md: <git hash-object blob id>
- docs/design/<design-id>/implementation-plan.md: <git hash-object blob id>
- <referenced high-level or module design path>: <git hash-object blob id>

GOAL:
<one observable outcome>

ACCEPTANCE_CRITERIA:
- <behavior that can be checked>

EXPECTED_PATHS:
- <path>

PROTECTED_PATHS:
- <human-owned baseline path or complete design package path that must not change>

ENTRY_SYMBOLS:
- <verified symbol or starting file>

DEPENDS_ON:
- <task id whose verified outcome is required first, or none>

ACCEPTANCE_COMMANDS:
- <exact bounded command>

COMMAND_ARTIFACTS:
- <exact repository-relative artifact path or bounded directory root, or none>

CONSTRAINTS:
- <public behavior or boundary that must remain unchanged>

KNOWN_FACTS:
- <fact verified from repository evidence>

KNOWN_FAILED_APPROACHES:
- <short fingerprint only, or none>
```
