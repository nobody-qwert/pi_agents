# Pi Nested Loop

Project-local prompts and agent definitions for supervising coding tasks in
[pi](https://github.com/badlogic/pi-mono). The subagent runtime is provided by
[pi-subagents](https://github.com/nicobailon/pi-subagents); this repository
contains only its orchestration prompt, task contract, and role definitions.

## Contents

| Path | Purpose |
| --- | --- |
| `.pi/prompts/supervise.md` | `/supervise` policy and final-completion prompt |
| `.pi/agents/orchestrator.md` | Routes the specialist workflow and returns verifier-backed checkpoints |
| `.pi/agents/investigator.md` | Read-only repository investigation and task routing |
| `.pi/agents/design-worker.md` | Read-only resolution of architectural decisions |
| `.pi/agents/coding-worker.md` | Implementation of one bounded outcome |
| `.pi/agents/verifier.md` | Independent per-packet contract and design-conformance verification |
| `.pi/agents/debugger.md` | Read-only diagnosis of a failed implementation attempt |
| `.pi/agents/reviewer.md` | Read-only review of a verified patch |
| `.pi/TASK_PACKET_TEMPLATE.md` | Handoff contract for implementation tasks |
| `assets/agent-orchestration.png` | Orchestration diagram asset (legacy) |

## Orchestration flow

```mermaid
graph TB
    U["User request"] --> S["Supervisor<br/>policy and completion authority"]
    S --> B(["Record Git baseline<br/>and protect existing changes"])
    B --> OR["Orchestrator<br/>.pi/agents/orchestrator.md"]
    OR --> I["Investigator<br/>.pi/agents/investigator.md"]
    I --> IR(["Status, evidence, and<br/>ready task packets"])

    IR -->|READY| P(["Canonical task packet<br/>.pi/TASK_PACKET_TEMPLATE.md"])
    IR -->|NEEDS_DESIGN| DC(["Design brief<br/>question, invariants, evidence, risks"])
    DC --> DW["Design worker<br/>.pi/agents/design-worker.md"]
    DW -->|READY| P

    P --> PV{Packet valid and<br/>protected paths clear?}
    PV -->|Yes| CW["Coding worker<br/>.pi/agents/coding-worker.md"]
    PV -->|No| BL["Concrete blocker<br/>reported to user"]
    CW --> WR(["Worker report"])
    WR -->|COMPLETED| V["Verifier<br/>contract, design, scope,<br/>and acceptance commands"]
    V --> Q{Verification passes?}
    Q -->|Yes| RQ{Review needed?}
    RQ -->|No| NP(["Next dependent packet<br/>or all packets verified"])
    RQ -->|Yes| RV["Reviewer<br/>.pi/agents/reviewer.md"]
    RV --> RVR(["Review verdict"])
    RVR -->|ACCEPT| NP

    WR -->|STUCK| FC(["Compact failure capsule"])
    Q -->|No| FI(["Inspect and normalize<br/>verification failure"])
    FI -->|Recovery allowance remains| FC
    FI -->|Recovery exhausted| BL
    FC --> D["Debugger<br/>.pi/agents/debugger.md"]
    D --> DE{New evidence and<br/>revised experiment?}
    DE -->|Yes| RP(["Revised task packet<br/>new evidence only"])
    DE -->|No| BL
    RP --> CW2["Replacement coding worker<br/>one attempt only"]
    CW2 --> WR2(["Replacement report"])
    WR2 -->|COMPLETED| V2["Verifier<br/>contract, design, scope,<br/>and acceptance commands"]
    V2 --> Q2{Verification passes?}
    Q2 -->|Yes| RQ
    Q2 -->|No| BL
    WR2 -->|STUCK<br/>BLOCKED_SCOPE<br/>ENVIRONMENT_BLOCKED| BL

    IR -->|ALREADY_SATISFIED| AS(["Supervisor bounded<br/>independent check"])
    AS --> OUT["Verified result returned to user"]
    NP -->|More packets| CP(["Compact checkpoint or<br/>fresh orchestrator handoff"])
    CP --> P
    NP -->|All verified| F(["Supervisor independently verifies<br/>final diff and command manifest"])
    F --> OUT

    IR -->|NEEDS_USER_DECISION<br/>BLOCKED_PROTECTED<br/>ENVIRONMENT_BLOCKED| BL["Concrete blocker<br/>reported to user"]
    DW -->|NEEDS_USER_DECISION<br/>ENVIRONMENT_BLOCKED| BL
    WR -->|BLOCKED_SCOPE<br/>ENVIRONMENT_BLOCKED| BL
    D -->|NEEDS_MORE_EVIDENCE<br/>ENVIRONMENT_BLOCKED| BL
    RVR -->|REJECT<br/>NEEDS_EVIDENCE| BL

    classDef agent fill:#2563eb,color:#ffffff,stroke:#1e3a8a,stroke-width:2px
    classDef supervisor fill:#111827,color:#ffffff,stroke:#374151,stroke-width:2px
    classDef handoff fill:#e5e7eb,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef decision fill:#fef3c7,color:#78350f,stroke:#d97706,stroke-width:2px
    classDef input fill:#f3f4f6,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef outcome fill:#dcfce7,color:#14532d,stroke:#16a34a,stroke-width:2px
    classDef blocker fill:#fee2e2,color:#7f1d1d,stroke:#dc2626,stroke-width:2px

    class OR,I,DW,CW,V,V2,D,RV agent
    class S supervisor
    class B,IR,DC,P,WR,NP,CP,FC,RP,AS,RVR,WR2,FI handoff
    class PV,Q,RQ,DE,Q2 decision
    class U input
    class OUT outcome
    class BL blocker
```

No model, provider, concurrency, or extension settings are checked in. Configure
them in the pi environment. The role definitions require subagents to run
sequentially, in the foreground, with fresh context. The only permitted
delegation hierarchy is supervisor → orchestrator → leaf specialist.

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

To use this setup in another repository, copy `.pi`. The role prompts are
self-contained and inherit that repository's own `AGENTS.md` or `CLAUDE.md`
when present. Keep repository-specific module boundaries, protected paths,
constraints, and verification commands in the target repository's instructions.

## Usage

From the configured repository root:

```text
/supervise <task and acceptance criteria>
```

The supervisor:

1. Records the Git baseline and treats pre-existing changes as protected.
2. Invokes the orchestrator once to route all specialist work.
3. Independently checks the final diff and reruns every command in the
   orchestrator's verification manifest before accepting completion.
4. Returns the verified result or a concrete blocker.

The orchestrator:

1. Runs the investigator and, only when needed, the design worker.
2. Sends ready task packets to coding workers sequentially.
3. Sends each completed packet to the verifier, which checks the implementation
   against its task packet and design decision and runs its exact acceptance
   commands before dependent work begins.
4. Runs the reviewer after verifier acceptance for large, risky,
   public-interface, or cross-responsibility changes.
5. On a worker or verifier failure, permits one debugger and at most one
   replacement coding worker when the diagnosis supplies a materially different
   experiment.

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
- Investigator, design, verifier, debugger, and reviewer roles are non-editing by
  instruction, not by a filesystem sandbox.
- Leaf agent definitions set fresh context and prohibit subagents. The
  orchestrator is the only delegating agent; all execution remains sequential,
  foreground, and unscheduled.
- Deterministic timeouts, filesystem isolation, and process enforcement require
  the extension or an external sandbox.
