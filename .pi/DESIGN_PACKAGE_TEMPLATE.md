# Durable Design Package

Every implementation task is anchored to one reviewed design package stored in
the target repository. The package records durable intent; task packets remain
the bounded execution contract for one worker.

## Required reviewed layout

```text
docs/design/<design-id>/
├── index.md
├── high-level.md
├── implementation-plan.md
├── modules/
│   └── <module-id>.md
└── status.md
```

`<design-id>` is a stable lowercase kebab-case identifier. Semantic design files
(`index.md`, `high-level.md`, `implementation-plan.md`, and `modules/*.md`) are
written only by the design workflow. `status.md` is written only by the status
writer. Coding workers never edit either group.

Before first design acceptance, the semantic candidate contains `index.md`,
`high-level.md`, `implementation-plan.md`, and `modules/*.md`; `status.md` may be
absent. The status writer may create it after the design verifier accepts the
candidate or after a terminal design rejection has enough safe ID/revision/path
metadata to persist `BLOCKED`. The design author never creates it. When
maintaining an existing package, the design author leaves the existing ledger
byte-for-byte unchanged.

## Reference and revision rules

- `DESIGN_REVISION` is a positive, monotonically increasing integer.
- Any normative change to a semantic design file increments the revision.
  Status-only changes do not.
- Normative requirements use exactly one Markdown heading in the form
  `## HLD-001 — <title>` or `## MOD-AUTH-001 — <title>`. IDs are unique across
  the package and never reused with changed meaning.
- Task packets reference requirements as `<path>::<requirement-id>`, never by
  line number or an automatically generated Markdown anchor.
- Each packet anchors `index.md`, `implementation-plan.md`, and every referenced
  high-level/module file by its `git hash-object` blob ID. A revision,
  requirement, plan, or blob mismatch makes the packet stale.
- A design is runnable only when `status.md` says `DESIGN_STATUS: READY`, its
  `REVIEWED_REVISION` equals the index revision, and its recorded design-verifier
  verdict is `ACCEPT`. Every current semantic-file blob ID must also match the
  verifier-authored `REVIEWED_FINGERPRINTS` persisted in the ledger.

## `index.md`

```text
# <design title>

DESIGN_ID: <design-id>
DESIGN_REVISION: <positive integer>

## Outcome
<user-visible outcome and scope>

## Documents
- high-level.md
- implementation-plan.md
- modules/<module-id>.md
```

The document inventory is exhaustive for semantic files in the current
revision. `status.md` is intentionally excluded because it is not semantic
design content.

## `high-level.md`

The high-level design records:

- goals and explicit non-goals;
- system boundaries and component responsibilities;
- dependency direction and principal data/control flows;
- cross-module contracts and invariants;
- compatibility, migration, and rollout constraints;
- material alternatives, decisions, and risks;
- links to every affected module specification.

Every normative statement that can constrain implementation is placed under one
exact `## HLD-NNN — <title>` requirement heading.

## `modules/<module-id>.md`

Each affected module receives one detailed design containing:

- `MODULE_ID` and its owned responsibility;
- non-responsibilities and boundary with neighboring modules;
- public and internal interfaces or contracts;
- allowed dependencies and owned state/data;
- important behavior and flows;
- failure and recovery semantics;
- module invariants and compatibility constraints;
- verification strategy.

Normative module requirements use exact headings such as
`## MOD-<MODULE>-NNN — <title>`.
Detailed designs describe contracts and invariants, not a line-by-line preview
of the implementation.

## `implementation-plan.md`

The plan contains one section per independently verifiable implementation task:

```text
## <TASK_ID>

GOAL:
<one observable outcome>

DESIGN_REFS:
- docs/design/<design-id>/high-level.md::<HLD-ID>
- docs/design/<design-id>/modules/<module-id>.md::MOD-<MODULE>-NNN

ACCEPTANCE_CRITERIA:
- <observable behavior>

EXPECTED_PATHS:
- <informed starting path>

ENTRY_SYMBOLS:
- <verified symbol or starting file>

DEPENDS_ON:
- <task id, or none>

ACCEPTANCE_COMMANDS:
- <exact bounded command>

COMMAND_ARTIFACTS:
- <exact repository-relative artifact path or directory root, or none>

CONSTRAINTS:
- <public behavior or boundary that remains unchanged>
```

The plan owns stable intent and dependencies. A runtime task packet must agree
with its plan entry while adding the current protected-path baseline, repository
facts, failed-approach fingerprints, and blob IDs for the index, plan, and
referenced design files.

`COMMAND_ARTIFACTS` is the complete pre-authorization for files that acceptance
commands may create or modify. Entries are exact repository-relative paths or
bounded directory roots, never globs or a repository-wide root. A worker report
cannot add authorization retrospectively; any undeclared command artifact fails
scope verification. Entries may not overlap protected paths or `docs/design/**`
and authorize command residue only, never implementation edits.
Every command boundary compares a complete repository filesystem inventory—path,
type, and content or symlink-target fingerprint—before and after execution,
excluding only VCS internals such as `.git/**`. This inventory, not Git status
alone, detects ignored and empty-directory residue.

## `status.md`

The status file is a verifier-backed ledger, not an agent work diary:

```text
# Design and implementation status

DESIGN_ID: <design-id>
DESIGN_REVISION: <positive integer>
DESIGN_STATUS: READY | BLOCKED
REVIEWED_REVISION: <positive integer, or none>
DESIGN_VERDICT: ACCEPT | REJECT | none

REVIEWED_FINGERPRINTS:
- <semantic design path>: <design-verifier git blob id>, or none

DESIGN_EVIDENCE:
- <design-verifier verdict and decisive evidence, or none>

## <TASK_ID>

STATE: PLANNED | VERIFIED_PENDING_FINAL | COMPLETE | BLOCKED
DEPENDS_ON:
- <task id, or none>
DESIGN_REFS:
- <path>::<requirement-id>
VERIFIER_VERDICT: ACCEPT | REJECT | none
REVIEWER_VERDICT: ACCEPT | REJECT | NOT_REQUIRED | none
FINAL_VERIFICATION: PENDING | PASS | FAIL | NOT_RUN
INNER_STATE_FINGERPRINTS:
- <non-status path or absent marker>: <fingerprint>, or none
FINAL_STATE_FINGERPRINTS:
- <non-status path or absent marker>: <fingerprint>, or none
EVIDENCE:
- <exact command/verdict/path, or none>
BLOCKER: <failure fingerprint, or none>
```

`REVIEWED_FINGERPRINTS` always describes `REVIEWED_REVISION`, which may differ
from the current `DESIGN_REVISION` while a newer candidate is blocked. It is
`none` when no revision has ever been accepted.

`INNER_STATE_FINGERPRINTS` is captured after the implementation verifier and any
required reviewer accept. It covers the complete cumulative task implementation
path set, all reviewed semantic design files, every pre-authorized command
artifact, and protected-path content/absence. `FINAL_STATE_FINGERPRINTS` covers
the same classes after the outer supervisor reruns the exact command manifest.
Both manifests exclude `status.md` and use explicit absent markers.
For any protected or command-artifact directory root, expand the snapshot to an
exhaustive sorted descendant-path inventory with one content/absence entry per
path; a later added or removed descendant is a mismatch. The package ledger is
the sole exclusion and is guarded separately by its compare-and-set fingerprint.

For `VERIFIED_PENDING_FINAL`, `INNER_STATE_FINGERPRINTS` is the authoritative
snapshot; for `COMPLETE`, `FINAL_STATE_FINGERPRINTS` is authoritative. A manifest
matches only when its exact path/absence inventory and every fingerprint
recompute identically. These snapshots are immutable while the task remains in
that state.

`DESIGN_VERDICT` records only an actual design-verifier verdict. A typed design
drift or failed outer design check sets `DESIGN_STATUS: BLOCKED` and records the
failure in `DESIGN_EVIDENCE`; it does not fabricate a verifier `REJECT`. When the
blocked revision is the previously accepted revision, its historical `ACCEPT`
verdict remains recorded.

Only durable facts are recorded. Worker claims such as `IN_PROGRESS` or
`IMPLEMENTED` are deliberately not persisted.

Allowed transitions are:

- a design-verifier `ACCEPT` authorizes `DESIGN_STATUS: READY`; accepting a new
  revision replaces the ledger revision and resets its current tasks;
- a design-verifier rejection or unresolved design conflict authorizes
  `DESIGN_STATUS: BLOCKED`;
- an outer design supervisor rejection authorizes `READY -> BLOCKED` while
  retaining the accepted revision and reviewed fingerprints as historical
  evidence;
- a new design revision resets all current plan tasks to `PLANNED`;
- verifier `ACCEPT` plus any required reviewer `ACCEPT` authorizes
  `PLANNED -> VERIFIED_PENDING_FINAL` and persists the checked inner-state
  fingerprint manifest;
- the outer supervisor's successful independent checks authorize
  `VERIFIED_PENDING_FINAL -> COMPLETE` and persist the exact checked non-status
  fingerprints;
- a terminal task failure authorizes the affected `PLANNED` or pending task and
  any pending/complete transitive dependants whose evidence relies on it to move
  to `BLOCKED`;
- new investigation/debugger evidence or a newly reverified failed dependency
  may authorize `BLOCKED -> PLANNED`;
- an exact mismatch in a pending task's inner snapshot or a complete task's
  final snapshot authorizes one atomic reset to `PLANNED` of the stale task and
  every transitive dependant currently pending or complete; the reset clears all
  verdict, verification, fingerprint, evidence, and blocker fields before
  topological revalidation;
- invalidating a design revision is a distinct package-wide transaction that may
  move any current-plan task, including `COMPLETE`, to `BLOCKED`; the previous
  completion evidence remains available in Git history.

Dependent implementation may begin only when every task in the full transitive
prerequisite closure is either
`VERIFIED_PENDING_FINAL` with currently matching `INNER_STATE_FINGERPRINTS` or
`COMPLETE` with currently matching `FINAL_STATE_FINGERPRINTS`. Finalization also
requires every prerequisite outside the atomic task set to be matching
`COMPLETE`, and every prerequisite inside it to be a matching pending or complete
task. User-facing completion requires `COMPLETE`. The status writer applies
transitions and dependency checks mechanically and never decides them.
