# Pi Nested Loop

Project-local prompts and agent definitions for supervising coding tasks in
[pi](https://github.com/badlogic/pi-mono). The subagent runtime is provided by
[pi-subagents](https://github.com/nicobailon/pi-subagents); this repository
contains only its project instructions and role definitions.

## Contents

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Shared repository, scope, verification, and execution rules |
| `.pi/prompts/supervise.md` | `/supervise` orchestration prompt |
| `.pi/agents/investigator.md` | Read-only repository investigation and task routing |
| `.pi/agents/design-worker.md` | Read-only resolution of architectural decisions |
| `.pi/agents/coding-worker.md` | Implementation of one bounded outcome |
| `.pi/agents/debugger.md` | Read-only diagnosis of a failed implementation attempt |
| `.pi/agents/reviewer.md` | Read-only review of a verified patch |
| `.pi/TASK_PACKET_TEMPLATE.md` | Handoff contract for implementation tasks |
| `assets/agent-orchestration.png` | Orchestration diagram asset (legacy) |

## Orchestration flow

```mermaid
graph TB
    U["User request"] --> S["Supervisor<br/>/supervise prompt"]
    S --> B(["Record Git baseline<br/>and protect existing changes"])
    B --> I["Investigator<br/>.pi/agents/investigator.md"]
    I --> IR(["Status, evidence, and<br/>ready task packets"])

    IR -->|READY| P(["Canonical task packet<br/>.pi/TASK_PACKET_TEMPLATE.md"])
    IR -->|NEEDS_DESIGN| DC(["Design capsule<br/>question, invariants, evidence, risks"])
    DC --> DW["Design worker<br/>.pi/agents/design-worker.md"]
    DW -->|READY| P

    P --> CW["Coding worker<br/>.pi/agents/coding-worker.md"]
    CW --> WR(["Worker report"])
    WR -->|COMPLETED| V(["Verify changed paths,<br/>hunks, and acceptance commands"])
    V --> Q{Verification passes?}
    Q -->|Yes| RQ{Review needed?}
    RQ -->|No| NP(["Next dependent packet<br/>or all packets verified"])
    RQ -->|Yes| RV["Reviewer<br/>.pi/agents/reviewer.md"]
    RV --> RVR(["Review verdict"])
    RVR -->|ACCEPT| NP

    WR -->|STUCK| FC(["Compact failure capsule"])
    Q -->|No| FC
    FC --> D["Debugger<br/>.pi/agents/debugger.md"]
    D -->|DIAGNOSED: revised experiment| RP(["Revised task packet<br/>new evidence only"])
    RP -->|One replacement attempt| CW

    IR -->|ALREADY_SATISFIED| AS(["Bounded independent check"])
    AS --> O["Verified result returned to user"]
    NP -->|More packets| P
    NP -->|All verified| O

    IR -->|NEEDS_USER_DECISION<br/>BLOCKED_PROTECTED<br/>ENVIRONMENT_BLOCKED| BL["Concrete blocker<br/>reported to user"]
    DW -->|NEEDS_USER_DECISION<br/>ENVIRONMENT_BLOCKED| BL
    WR -->|BLOCKED_SCOPE<br/>ENVIRONMENT_BLOCKED| BL
    D -->|NEEDS_MORE_EVIDENCE<br/>ENVIRONMENT_BLOCKED| BL
    RVR -->|REJECT<br/>NEEDS_EVIDENCE| BL
    FC -->|No revised experiment| BL

    classDef agent fill:#2563eb,color:#ffffff,stroke:#1e3a8a,stroke-width:2px
    classDef supervisor fill:#111827,color:#ffffff,stroke:#374151,stroke-width:2px
    classDef handoff fill:#e5e7eb,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef decision fill:#fef3c7,color:#78350f,stroke:#d97706,stroke-width:2px
    classDef input fill:#f3f4f6,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef outcome fill:#dcfce7,color:#14532d,stroke:#16a34a,stroke-width:2px
    classDef blocker fill:#fee2e2,color:#7f1d1d,stroke:#dc2626,stroke-width:2px

    class I,DW,CW,D,RV agent
    class S supervisor
    class B,IR,DC,P,WR,V,NP,FC,RP,AS,RVR handoff
    class Q,RQ decision
    class U input
    class O outcome
    class BL blocker
```

No model, provider, concurrency, or extension settings are checked in. Configure
them in the pi environment. The project instructions require subagents to run
sequentially, in the foreground, with fresh context, and without nested
subagents.

## Requirements

- pi with project-local prompts and agents enabled
- a model provider configured in pi
- the `pi-subagents` extension
- a trusted target repository with concrete verification commands

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

To use this setup in another repository, copy `.pi`, then merge the relevant
rules from `AGENTS.md` into that repository's existing instructions. Do not
overwrite existing project instructions blindly. Update repository-specific
module boundaries, protected paths, constraints, and verification commands.

## Usage

From the configured repository root:

```text
/supervise <task and acceptance criteria>
```

The supervisor:

1. Records the Git baseline and treats pre-existing changes as protected.
2. Runs the investigator to locate ownership, constraints, and verification
   commands.
3. Runs the design worker only when the investigator reports `NEEDS_DESIGN`.
4. Sends ready task packets to the coding worker sequentially.
5. Checks changed paths and hunks, then independently runs every packet's
   acceptance commands.
6. Runs the reviewer after verification for large, risky, public-interface, or
   cross-responsibility changes.
7. On `STUCK` or a repeated verification failure, permits one debugger and at
   most one replacement coding worker when the diagnosis provides a materially
   different experiment.
8. Returns the verified result or a concrete blocker.

## Task packets

Each packet defines:

- one observable `GOAL` and its `ACCEPTANCE_CRITERIA`;
- `EXPECTED_PATHS` as informed starting points, not an exhaustive allowlist;
- strict `PROTECTED_PATHS` that must not change;
- verified `ENTRY_SYMBOLS` and task dependencies;
- exact `ACCEPTANCE_COMMANDS`;
- constraints, known facts, and fingerprints of failed approaches.

The coding worker may change paths outside `EXPECTED_PATHS` when required by the
same outcome, but must return `BLOCKED_SCOPE` if the outcome must broaden or a
protected path must change.

## Boundaries

- Existing uncommitted changes are human-owned.
- Workers must not weaken tests, bypass checks, or perform unrelated cleanup.
- Investigator, design, debugger, and reviewer roles are non-editing by
  instruction, not by a filesystem sandbox.
- Agent definitions set fresh context and prohibit nested subagents; the
  supervisor also prohibits parallel, background, asynchronous, and scheduled
  execution.
- Deterministic timeouts, filesystem isolation, and process enforcement require
  the extension or an external sandbox.
