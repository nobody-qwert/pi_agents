# Durable Design Package

Every implementation task is anchored to one reviewed design package. The
package records durable intent; task packets are bounded execution contracts.

## Required layout

```text
docs/design/<design-id>/
├── index.md
├── high-level.md
├── implementation-plan.md
├── modules/
│   └── <module-id>.md
└── status.md
```

`<design-id>` is stable lowercase kebab-case. The design workflow alone writes
semantic files (`index.md`, `high-level.md`, `implementation-plan.md`, and
`modules/*.md`). The status writer alone writes `status.md`. Coding workers never
edit `docs/design/**`.

Before first acceptance, `status.md` may be absent. The status writer may create
it after verifier acceptance or a safely identified terminal design rejection.
The design author never creates or edits it.

## References and revisions

- `DESIGN_REVISION` is a positive monotonically increasing integer.
- Every normative semantic change increments it; status-only changes do not.
- Normative requirements use one canonical heading such as
  `## HLD-001 — <title>` or `## MOD-AUTH-001 — <title>` and never reuse an ID
  with changed meaning.
- Packets reference requirements as `<path>::<requirement-id>`.
- Each packet fingerprints `index.md`, `implementation-plan.md`, and every
  referenced high-level/module file. A mismatch makes the packet stale.
- A design is runnable only when `status.md` says `READY`, its reviewed revision
  equals the index revision, its verdict is `ACCEPT`, and every semantic file
  matches `REVIEWED_FINGERPRINTS`.

These fingerprints identify document content. They may be computed from the
working tree and do not require a clean repository or committed files.

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

The inventory is exhaustive for semantic files; `status.md` is excluded.

## `high-level.md`

Record goals and non-goals, boundaries and ownership, dependency direction,
principal flows, cross-module contracts and invariants, compatibility/migration
constraints, material alternatives, decisions, risks, and links to affected
module specifications. Put every normative statement under one exact
`## HLD-NNN — <title>` heading.

## `modules/<module-id>.md`

Each affected module records its ID and responsibility, non-responsibilities,
interfaces, allowed dependencies, owned state, important behavior, failure and
recovery semantics, invariants, compatibility constraints, and verification
strategy. Normative headings use `## MOD-<MODULE>-NNN — <title>`. Describe stable
contracts rather than predicting code line by line.

## `implementation-plan.md`

Use one section per independently verifiable task:

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

CONSTRAINTS:
- <public behavior or boundary that remains unchanged>
```

Tasks own stable intent and dependencies. Expected paths guide investigation and
implementation but are never an exhaustive allowlist. Acceptance commands are
executed by the worker, independent verifier, and outer supervisor.

## `status.md`

The status file is a verifier-backed ledger, not an agent diary:

```text
# Design and implementation status

DESIGN_ID: <design-id>
DESIGN_REVISION: <positive integer>
DESIGN_STATUS: READY | BLOCKED
REVIEWED_REVISION: <positive integer, or none>
DESIGN_VERDICT: ACCEPT | REJECT | none

REVIEWED_FINGERPRINTS:
- <semantic design path>: <design-verifier content fingerprint>, or none

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
EVIDENCE:
- <exact command/verdict/path, or none>
BLOCKER: <failure fingerprint, or none>
```

`REVIEWED_FINGERPRINTS` describes `REVIEWED_REVISION`, which may differ from a
new blocked candidate revision. It is `none` when nothing has been accepted.
`DESIGN_VERDICT` records only an actual verifier verdict; design drift or outer
rejection must not fabricate `REJECT`.

Only durable workflow facts are recorded. Worker claims such as `IN_PROGRESS`
or `IMPLEMENTED` are not persisted. The ledger contains no implementation
workspace fingerprints.

Allowed transitions:

- design-verifier `ACCEPT` authorizes design `READY`;
- verifier rejection, unresolved design conflict, or failed outer design check
  authorizes design `BLOCKED`;
- a new ready design revision replaces current task entries as `PLANNED`;
- implementation verifier `ACCEPT` plus required reviewer `ACCEPT` authorizes
  `PLANNED -> VERIFIED_PENDING_FINAL`;
- successful outer execution of the exact command manifest authorizes
  `VERIFIED_PENDING_FINAL -> COMPLETE`;
- terminal failure authorizes the affected task and unfinished dependants to
  become `BLOCKED`;
- materially new investigation/debugger evidence or a newly reverified
  prerequisite may authorize `BLOCKED -> PLANNED`.

A dependent task may begin when every transitive prerequisite is
`VERIFIED_PENDING_FINAL` or `COMPLETE`. Finalization is dependency-closed and
user-facing completion requires `COMPLETE`. The status writer applies authorized
transitions mechanically using the current `status.md` content fingerprint as a
compare-and-set guard.
