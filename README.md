# Pi Nested Loop

Project-local configuration for supervising coding tasks with small, isolated
subagents in [pi](https://github.com/badlogic/pi-mono). The supervisor delegates
one bounded task at a time, verifies the result, and starts a fresh diagnostic
or implementation context when a worker gets stuck.

This repository contains the instruction and configuration layer. The subagent
runtime is provided separately by
[pi-subagents](https://github.com/nicobailon/pi-subagents).

## What this repository provides

- A `/supervise` prompt for task decomposition, delegation, and verification.
- Separate worker roles for implementation, debugging, design, and review.
- A compact task-packet template for reproducible handoffs.
- Shared repository rules for scope control, modularity, and test discipline.
- Sequential execution settings for environments where all agents share one
  model server.
- Explicit stop conditions for repeated failures and unproductive repair loops.

## Repository layout

```text
.
├── AGENTS.md
└── .pi/
    ├── TASK_PACKET_TEMPLATE.md
    ├── agents/
    │   ├── coding-worker.md
    │   ├── debugger.md
    │   ├── design-worker.md
    │   └── reviewer.md
    ├── prompts/
    │   └── supervise.md
    └── settings.json
```

| Path | Purpose |
| --- | --- |
| `AGENTS.md` | Shared rules loaded by the supervisor and project subagents |
| `.pi/prompts/supervise.md` | Outer-supervisor workflow exposed as `/supervise` |
| `.pi/agents/coding-worker.md` | Implements one narrow task in a fresh context |
| `.pi/agents/debugger.md` | Diagnoses a failed attempt without editing files |
| `.pi/agents/design-worker.md` | Splits broad work into module-sized task packets |
| `.pi/agents/reviewer.md` | Reviews a completed patch independently |
| `.pi/TASK_PACKET_TEMPLATE.md` | Schema for bounded worker handoffs |
| `.pi/settings.json` | Model allowlist and subagent concurrency limits |

## Requirements

- pi with project-local prompts and agents enabled.
- A model provider configured in pi.
- The `pi-subagents` extension.
- A trusted target repository with concrete test and build commands.

Install the subagent extension through pi:

```bash
pi install npm:pi-subagents
```

Review third-party extensions before installing them. Extensions run with the
permissions of the user running pi.

## Setup

Clone the repository and enter it:

```bash
git clone https://github.com/nobody-qwert/pi_agents.git
cd pi_agents
```

The checked-in agent definitions currently use this pi model identifier:

```text
lmstudio/qwen3.6-27b@q4_k_m
```

If your provider or model differs, update the model identifier consistently in:

- `.pi/settings.json`
- `.pi/agents/*.md`
- `.pi/prompts/supervise.md`
- `AGENTS.md`

The identifier must match one reported by:

```bash
pi --list-models
```

Keep the concurrency limits in `.pi/settings.json` at `1` when the supervisor
and workers share a model instance with limited resources. Increase them only
when the serving environment is intentionally configured for concurrent
generation.

## Use in another project

Copy the `.pi` directory into the target repository, then merge the relevant
rules from `AGENTS.md` with that repository's existing instructions. Do not
blindly overwrite an existing `AGENTS.md`.

At minimum, customize:

1. Model identifiers and provider configuration.
2. Repository module boundaries and protected paths.
3. Test, type-check, lint, and build commands.
4. Limits on worker scope, retries, and allowed files.

Trust project-local pi configuration only when you trust the repository that
contains it.

## Usage

Start pi from the root of the configured project:

```bash
pi
```

Then delegate a task with:

```text
/supervise describe the coding task and acceptance criteria
```

The expected control flow is:

```mermaid
graph TB
    U["User request"] --> S0["Outer supervisor<br/>.pi/prompts/supervise.md"]
    S0 --> B{"Is the task broad?"}

    B -->|Yes| H1(["Design request"])
    H1 --> DW["Design worker<br/>.pi/agents/design-worker.md"]
    DW --> H2(["Task decomposition"])
    H2 --> S1["Supervisor<br/>Prepares implementation"]
    B -->|No| S1

    S1 --> H3(["Lean task packet<br/>.pi/TASK_PACKET_TEMPLATE.md"])
    H3 --> CW["Coding worker<br/>.pi/agents/coding-worker.md"]
    CW --> H4(["Patch and worker report<br/>or STUCK status"])
    H4 --> S2["Supervisor<br/>Inspects diff and runs acceptance commands"]
    S2 --> V{"Verification result"}

    V -->|Pass, low risk| O["Result returned to user<br/>Verified result or concrete blocker"]

    V -->|Pass, risky| H5(["Verified risky patch"])
    H5 --> R["Reviewer<br/>.pi/agents/reviewer.md"]
    R --> H6(["Review verdict"])
    H6 --> S3["Supervisor<br/>Evaluates review"]
    S3 --> O

    V -->|Fail or stuck| H7(["Compact failure capsule"])
    H7 --> D["Debugger<br/>.pi/agents/debugger.md"]
    D --> H8(["Root-cause diagnosis"])
    H8 --> S4["Supervisor<br/>Creates recovery task"]
    S4 --> H9(["Revised task packet"])
    H9 --> CW2["Replacement coding worker<br/>.pi/agents/coding-worker.md"]
    CW2 --> H10(["Replacement patch and report"])
    H10 --> S5["Supervisor<br/>Reruns acceptance commands"]
    S5 --> V2{"Replacement checks pass?"}
    V2 -->|Yes| O
    V2 -->|No, report blocker| O

    classDef agent fill:#2563eb,color:#ffffff,stroke:#1e3a8a,stroke-width:2px
    classDef handoff fill:#e5e7eb,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef decision fill:#fef3c7,color:#78350f,stroke:#d97706,stroke-width:2px
    classDef input fill:#f3f4f6,color:#111827,stroke:#6b7280,stroke-width:1px
    classDef outcome fill:#dcfce7,color:#14532d,stroke:#16a34a,stroke-width:2px

    class S0,S1,S2,S3,S4,S5,DW,CW,R,D,CW2 agent
    class H1,H2,H3,H4,H5,H6,H7,H8,H9,H10 handoff
    class B,V,V2 decision
    class U input
    class O outcome
```

Dark-blue boxes are model-driven agent roles. All supervisor phase boxes use
`.pi/prompts/supervise.md`; specialized agents show their own definition files.
Gray pill-shaped nodes are handoff notes or artifacts rather than agents. Amber
diamonds are supervisor decisions, and green is the result returned to the
user. The flow is intentionally unfolded instead of drawing loop-back arrows so
Markdown renderers can space the branches without overlapping labels.

Subagent calls should use project scope, a fresh context, and foreground
execution. The supplied supervisor prompt enforces that policy at the
instruction level.

## Task design

A useful worker task contains:

- one observable goal;
- explicit acceptance criteria;
- a small allowed-path list;
- likely entry symbols;
- exact verification commands;
- protected behavior and scope constraints;
- short fingerprints of approaches that already failed.

The worker should return a structured result rather than a long transcript. The
supervisor treats that result as a claim and verifies the diff and commands
independently.

## Failure handling

The included roles follow a bounded recovery process:

1. A coding worker makes the smallest cohesive change and runs the named checks.
2. It stops if the same normalized failure survives two distinct fixes.
3. A fresh debugger examines a compact failure summary without editing files.
4. The supervisor creates a revised packet for a replacement worker.
5. If the task still cannot progress, the supervisor splits it or reports the
   concrete blocker.

This keeps failed theories and large transcripts out of replacement workers'
contexts.

## Safety and limitations

- Existing uncommitted changes are treated as human-owned.
- Workers may not weaken tests or broaden their allowed paths to claim success.
- Read-only design, debugging, and review roles are separated from implementation.
- Parallel, background, scheduled, and nested subagents are disabled by policy.
- Markdown instructions guide agent behavior but cannot provide hard process or
  filesystem isolation.
- Deterministic enforcement such as timeouts, path allowlists, event-stream
  inspection, and forced termination requires an extension or external sandbox.

Before adopting this setup for a production repository, verify the extension,
provider, model, permissions, and acceptance commands in a disposable branch.
