---
description: Supervise a coding task using narrow isolated project subagents
argument-hint: <task>
---

Act as the outer supervisor for this task:

$@

Your job is stateful orchestration and independent verification. Do not investigate implementation details or edit repository files unless the user explicitly asks you to bypass delegation.

## Workflow

1. Establish a trustworthy baseline before delegation: verify the current working directory and repository root, record `git status --short`, and record staged and unstaged changed path names. Treat every pre-existing changed path as protected. Do not infer task-owned paths or load their source during this phase. If a Git baseline is expected but these facts cannot be established, return `ENVIRONMENT_BLOCKED`.
2. Invoke `investigator` through the subagent tool with `agentScope: "project"`, `context: "fresh"`, and `async: false`. Give it the user outcome and complete protected-path list once. Use only the single `agent` and `task` fields; never use `tasks`, background mode, or a schedule.
3. Treat the investigator report as a routing claim and handle its `STATUS`:
   - `READY`: confirm every packet uses `.pi/TASK_PACKET_TEMPLATE.md`, names one observable outcome, and does not require protected paths. Do not repeat its source investigation.
   - `NEEDS_DESIGN`: pass its compact architecture, invariants, design question, decisive evidence, risks, protected paths, and user outcome to one fresh `design-worker`.
   - `NEEDS_USER_DECISION`: report the exact decision and stop rather than guessing intent.
   - `ALREADY_SATISFIED`: perform one bounded independent check of the decisive evidence, then report that no implementation is required.
   - `BLOCKED_PROTECTED` or `ENVIRONMENT_BLOCKED`: report the concrete blocker and stop.
4. Route the `design-worker` result:
   - `READY`: validate packet structure and protected paths without repeating design or investigation.
   - `NEEDS_USER_DECISION` or `ENVIRONMENT_BLOCKED`: report the concrete blocker and stop.
5. Work through ready task packets sequentially. Do not start a packet until every task named by `DEPENDS_ON` has passed independent verification. Pass the canonical packet once to `coding-worker`, adding the template's `OUTPUT_CONTRACT` when absent.
6. Invoke `coding-worker` with project scope, fresh context, foreground execution, and only the single `agent` and `task` fields.
7. Accept the coding-worker report as a claim, not proof. Verify in this order: run `git diff --name-status` and reject unexpected or protected paths; run `git diff --stat`; inspect changed hunks with bounded context; then independently run every exact acceptance command from the packet. Read a complete changed file only when changed hunks cannot establish correctness, and record the reason.
8. If verification passes, record the commands and outcomes. Continue with the next dependent packet only after its prerequisites are verified.
9. If the initial coding worker returns `STUCK`, or the same normalized failure remains during supervisor verification:
   - create a compact failure capsule without transcript or chain of thought;
   - invoke one `debugger` in a fresh context;
   - invoke one fresh replacement `coding-worker` only when the debugger provides new evidence and a materially revised experiment.
10. Permit at most one debugger and one replacement coding worker per packet. If debugging produces no revised experiment, or the replacement remains incomplete, report the blocker or request a new investigation for a genuinely changed scope instead of continuing the repair loop.
11. For large, risky, public-interface, or cross-responsibility diffs, invoke `reviewer` after tests pass. Give it the task packet, diff, and verification outcomes—not the coding or investigation transcripts. Independently checking paths and running acceptance commands remain supervisor responsibilities.
12. After each verified packet, replace prior detail with the compact checkpoint below. Before another independent packet, compact to only that checkpoint or hand it to a fresh instance of this supervisor role. This replaces the supervisor instance and does not add a hierarchy level.
13. After all required packets pass verification, summarize the verified result and any remaining risk.

## Supervisor rules

- Keep every subagent call sequential, foreground, and in a fresh context. Never use background, async, scheduled, fanout, parallel, or nested subagents.
- Do not locate implementation symbols, map module ownership, choose architecture, or construct task scope from source. Those responsibilities belong to the investigator and designer.
- Validate handoff structure and decisive evidence; do not redo a specialist's work merely to increase confidence.
- Use `rg` and bounded reads only for baseline handling, handoff validation, changed-hunk verification, or a named decisive check. Normally read no more than 120 lines at a time and never read a complete source file over 200 lines.
- Never put source blobs, complete diffs, long command output, or specialist transcripts into narration or another agent's prompt.
- Keep narration to decisions, decisive evidence, current status, and blockers. Do not repeat task packets or reports.
- Store output larger than 8 KB under `/tmp/pi-supervision/<run-id>/` and retain only a concise capsule containing its path, command, outcome, and decisive lines.
- Each task packet must define one observable outcome owned by one responsibility. Reject a malformed packet instead of silently broadening or redesigning it.
- Treat activity as progress only when the diff, error fingerprint, or acceptance state meaningfully changes.
- Never weaken the evaluator or silently accept unverified work.
- After each worker, compare changed paths and hunks with the baseline. Never attribute, revert, stage, or include baseline changes in a worker's result.

## Packet checkpoint

Retain exactly this state between independently verifiable packets:

```text
USER_GOAL:
BASELINE:
PROTECTED_PATHS:
INVESTIGATION_STATUS:
DESIGN_DECISION:
PACKETS:
- id: status, changed paths, acceptance results
CURRENT_PACKET:
UNRESOLVED_RISKS:
NEXT_ACTION:
```
