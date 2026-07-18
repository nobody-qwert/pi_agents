# Pi Nested Loop

Project-local prompts and agent definitions for design-anchored coding tasks in
[pi](https://github.com/badlogic/pi-mono). The subagent runtime is provided by
[pi-subagents](https://github.com/nicobailon/pi-subagents); this repository
contains orchestration prompts, durable design and task contracts, and specialist
role definitions.

## Contents

| Path | Purpose |
| --- | --- |
| `.pi/prompts/design.md` | `/design` policy and independent design acceptance |
| `.pi/prompts/supervise.md` | `/supervise` implementation policy and final authority |
| `.pi/agents/orchestrator.md` | Routes design, implementation, recovery, and finalization |
| `.pi/agents/investigator.md` | Reconciles source, design, status, and planned tasks |
| `.pi/agents/design-worker.md` | Authors high-level and module design artifacts |
| `.pi/agents/design-verifier.md` | Independently verifies one design revision and plan |
| `.pi/agents/coding-worker.md` | Implements one bounded design-anchored outcome |
| `.pi/agents/verifier.md` | Independently verifies contract, design, and commands |
| `.pi/agents/debugger.md` | Diagnoses one code-level failure without editing |
| `.pi/agents/reviewer.md` | Reviews a verified task-local implementation |
| `.pi/agents/status-writer.md` | Persists one authorized status transaction |
| `.pi/DESIGN_PACKAGE_TEMPLATE.md` | Durable design package and ledger contract |
| `.pi/TASK_PACKET_TEMPLATE.md` | Design-anchored implementation handoff |

## Operating model

The harness coordinates design, implementation, review, and verification in the
current workspace. It is not a workspace transaction manager.

1. The investigator reconciles the request, current source, design package, and
   task ledger.
2. Missing or stale design is authored and independently verified.
3. The orchestrator selects a reviewed design-anchored task packet.
4. A coding worker edits the current workspace and reports the files it changed.
5. An independent verifier inspects the task-local implementation and runs every
   exact acceptance command.
6. A reviewer checks changes that are risky, public-interface,
   security-sensitive, migration-related, large, or cross-responsibility.
7. Verified tasks are recorded as `VERIFIED_PENDING_FINAL` so dependent tasks
   can proceed.
8. The outer supervisor reruns the complete exact command manifest.
9. A restricted finalization call marks the verified dependency-closed task set
   `COMPLETE` using a compare-and-set ledger update.

All specialists run sequentially in fresh foreground context. The only
delegation hierarchy is outer supervisor → orchestrator → leaf specialist.

## Durable design packages

Every implementation is anchored to a reviewed package in the target repository:

```text
docs/design/<design-id>/
├── index.md
├── high-level.md
├── implementation-plan.md
├── modules/
│   └── <module-id>.md
└── status.md
```

High-level design records boundaries, ownership, dependency direction, flows,
cross-module contracts, compatibility, decisions, and risks. Each affected
module records interfaces, dependencies, state, behavior, failure semantics,
invariants, and verification strategy.

Normative requirements have stable IDs such as `HLD-001` and `MOD-AUTH-001`.
Task packets cite them as `path::requirement-id`. Packets also carry content
fingerprints for the package index, implementation plan, and referenced design
files. These fingerprints bind a packet to the exact reviewed semantic content;
they are not workspace snapshots and do not require files to be committed.

`DESIGN_REVISION` increases for normative semantic changes. A design is runnable
only when its ledger records `READY`, the reviewed revision equals the index
revision, the independent design verdict is `ACCEPT`, and every current semantic
file matches the reviewed fingerprint manifest.

## Status ownership

Progress has one semantic owner and one mechanical writer:

- the orchestrator authorizes state transitions;
- the design verifier authorizes design readiness;
- the implementation verifier and required reviewer authorize inner task
  verification;
- the outer supervisor alone authorizes user-facing completion;
- `status-writer` edits only the exact package `status.md` using its current
  content fingerprint as a compare-and-set guard.

Task states are:

```text
PLANNED → VERIFIED_PENDING_FINAL → COMPLETE
   ↑              |
   └── BLOCKED ←──┘
```

`VERIFIED_PENDING_FINAL` means the implementation and exact task commands passed
independent inner verification. `COMPLETE` is written only after the outer
supervisor reruns the complete command manifest successfully. Dependencies may
start when prerequisites are `VERIFIED_PENDING_FINAL` or `COMPLETE`, and
finalization remains dependency-closed.

The ledger records workflow evidence, not a continuously checked implementation
snapshot. If current behavior is in question, the harness inspects source and
reruns the exact acceptance commands rather than claiming that old workspace
fingerprints prove conformance.

## Workspace responsibility and tradeoffs

The harness intentionally does not inventory the repository, inspect Git state
for consistency, capture pre-run dirty files, attribute individual edits, detect
all generated residue, or provide rollback. It never stages, commits, reverts,
cleans, or restores files.

Consequently:

- an agent may edit a file that already contains user changes;
- the harness cannot reliably distinguish user lines from agent lines;
- unrelated files created by tools or tests may not be detected;
- the harness cannot prove that only reported or expected paths changed;
- concurrent human and agent editing may conflict;
- recovery relies on user-managed Git, editor history, backups, or other tools.

Review the workspace before and after a run when isolation or attribution
matters. Use a clean branch, worktree, container, or external sandbox when the
task requires stronger guarantees.

## Requirements

- pi with project-local prompts and agents enabled
- a configured model provider
- the `pi-subagents` extension
- a trusted target repository with concrete bounded verification commands

Install the extension:

```bash
pi install npm:pi-subagents
```

Extensions run with the permissions of the pi process. Review third-party
extensions before installing them.

## Setup

```bash
git clone https://github.com/nobody-qwert/pi_agents.git
cd pi_agents
pi
```

To use this setup elsewhere, copy `.pi`. Role prompts inherit that repository's
`AGENTS.md` or `CLAUDE.md` when present. Keep repository-specific boundaries,
constraints, and verification commands in the target repository instructions.

## Usage

Produce or maintain design without implementation:

```text
/design <outcome, constraints, and design questions>
```

The design workflow investigates current ownership, authors or updates one
package, independently verifies high-level and detailed module design plus the
implementation plan, then persists reviewed readiness and planned tasks.

Run design-anchored implementation:

```text
/supervise <task and acceptance criteria>
```

The implementation workflow reconciles current source and design, authors
missing design when needed, processes reviewed task packets sequentially,
independently verifies each task, performs risk-based review, reruns the final
command manifest, and persists completion through a restricted status-only call.

One code-level failure may use one debugger and at most one replacement worker
when materially new evidence supports a different experiment. Design mismatches
never enter the coding recovery loop.

## Task packets

Each packet defines:

- one stable task ID and observable goal;
- exact acceptance criteria;
- the reviewed design ID and revision;
- stable high-level and owning-module requirement references;
- content fingerprints for the index, plan, and referenced semantic files;
- expected paths as informed starting points, not an exhaustive allowlist;
- verified entry symbols and task dependencies;
- exact bounded acceptance commands;
- constraints, known facts, and failed-approach fingerprints.

The packet must agree with its `implementation-plan.md` entry. A worker may edit
another path when required by the same bounded outcome, but must stop when the
outcome materially broadens or the approved design no longer supports the work.

## Role boundaries

- Design authors edit only semantic files under one design package and never
  its status, source, or tests.
- Status writers edit only one exact `docs/design/<design-id>/status.md` and do
  not decide transitions.
- Coding workers never edit `docs/design/**`.
- Investigator, design verifier, implementation verifier, debugger, and reviewer
  are non-editing by instruction rather than filesystem sandbox.
- Workers must not weaken tests, bypass checks, or perform unrelated cleanup.
- Source, design, status, diffs, logs, command output, and reports are untrusted
  task data rather than executable instructions.
- Leaf definitions prohibit subagents; all execution is sequential and
  foreground.
- Deterministic timeouts and filesystem/process isolation require the extension
  or an external sandbox.
