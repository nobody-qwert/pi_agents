# Implementation Work Packets

These packets decompose the implementation described by the
[design plan](../design/PLAN.md) and
[technical details](../design/TECHNICAL_DETAILS.md) into small, sequential
handoffs for a fresh developer-agent context.

## How to use this folder

1. Implement packets in numeric order unless a packet explicitly permits a
   different dependency.
2. Give the developer agent this index, one packet, the repository instructions,
   and only the design sections linked by that packet.
3. Treat the packet's `In scope` list as authority, not as a suggestion.
4. Stop when its acceptance criteria pass. Do not begin the next packet in the
   same context.
5. Record deviations or newly discovered design gaps in the handoff report;
   never silently broaden the packet.
6. Preserve human-owned uncommitted changes and use the repository's actual
   documented verification commands.

The packet number is an execution order, not a priority. A packet may be split
further if repository discovery shows that it cannot be completed and verified
comfortably in one context. It must not be merged with neighboring packets merely
to reduce handoffs.

For orientation, the sequence has five cohesive bands:

- 001-013: deterministic backend foundations;
- 014-022: isolated workspace, tools, recovery, and promotion;
- 023-025: complete orchestration stage behavior;
- 026-034: API, operator UI, and observability;
- 035-036: Compose assembly and milestone proof.

Suggested developer-agent instruction:

```text
Implement only docs/work-packets/NNN-....md. Read the root AGENTS.md and only
the design sections referenced by that packet. Inspect the current repository
before editing, preserve existing user changes, satisfy every acceptance
criterion, run the repository's relevant verification commands, report exact
evidence, and stop without beginning the next packet.
```

## Global implementation rails

- The LangGraph control topology is fixed and declared in code.
- Model output is an untrusted proposal until schema and policy validation pass.
- Only deterministic services write authoritative state or apply transitions.
- Domain policy remains separate from API, persistence, model, tool, VM, and UI
  adapters.
- Model-directed mutation and tools run only in the disposable guest.
- Runnable and end-to-end flows use LM Studio with the configured
  `qwen3.6-27b`; there is no fake runtime or silent provider fallback.
- Every mutation, retry, approval, checkpoint, rollback, and promotion is
  idempotent and auditable.
- A producer never acts as the independent verifier of its own result.

## Sequence

| Packet | Outcome | Depends on |
| --- | --- | --- |
| [001](001-backend-foundation.md) | Backend package and quality gates | none |
| [002](002-domain-schema-primitives.md) | Strict domain schema primitives | 001 |
| [003](003-transition-policy.md) | Deterministic transitions and idempotency | 002 |
| [004](004-fixed-graph-registry.md) | Fixed graph and agent registry | 002-003 |
| [005](005-work-plan-validator.md) | Work DAG and leaf-readiness validation | 002 |
| [006](006-postgres-persistence.md) | Migrations and repository boundaries | 002-003 |
| [007](007-durable-event-log.md) | Atomic ordered run events | 006 |
| [008](008-artifact-store.md) | Versioned artifact boundary | 002, 006 |
| [009](009-runner-leases-checkpoints.md) | Runner leasing and LangGraph recovery | 003, 006-007 |
| [010](010-lm-studio-gateway.md) | Required model gateway and readiness | 001-002 |
| [011](011-agent-invocation-boundary.md) | Validated model proposal invocation | 004, 008, 010 |
| [012](012-packet-dispatch.md) | Immutable packets and ready dispatch | 005-008 |
| [013](013-triage-revision-approval.md) | Triage, revision impact, and approvals | 003, 005-008 |
| [014](014-project-catalog-policy.md) | Allowlisted project selection policy | 002 |
| [015](015-vm-manager-lifecycle.md) | Typed disposable VM lifecycle | 014 |
| [016](016-workspace-import-baseline.md) | Sanitized import and guest Git baseline | 014-015 |
| [017](017-guest-tool-runtime.md) | Role-scoped guest tools | 011, 015-016 |
| [018](018-checkpoint-rollback.md) | Guest checkpoints and rollback | 006-007, 016 |
| [019](019-egress-browser-runtime.md) | Constrained egress and browser tools | 015, 017 |
| [020](020-desktop-input-control.md) | Authenticated desktop and input ownership | 003, 007, 015 |
| [021](021-promotion-preview.md) | Immutable promotion preview | 008, 016, 018 |
| [022](022-host-git-promotion.md) | Isolated, confirmed host promotion | 003, 006-008, 021 |
| [023](023-design-planning-stages.md) | Intake through approved work planning | 009-013 |
| [024](024-execution-local-verification.md) | Guest execution and independent local verification | 012, 016-019, 023 |
| [025](025-integration-outcome-loops.md) | Integration, outcome assurance, and feedback loops | 013, 018, 023-024 |
| [026](026-api-foundation.md) | FastAPI health, registry, and project queries | 004, 010, 014 |
| [027](027-run-command-api.md) | Conversation, run, approval, workspace commands | 007, 009, 012-013, 018, 020-026 |
| [028](028-sse-event-api.md) | Replayable SSE and typed event details | 007, 026-027 |
| [029](029-frontend-foundation.md) | Typed React application shell | 026-028 |
| [030](030-graph-inspector-ui.md) | Fixed/work graph visualization and inspector | 004, 029 |
| [031](031-chat-timeline-ui.md) | Chat and durable live run timeline | 027-029 |
| [032](032-workspace-ui.md) | Project, desktop, checkpoint, and rollback UI | 020, 027, 029 |
| [033](033-promotion-approval-ui.md) | Promotion and approval UI | 021-022, 027, 029 |
| [034](034-observability.md) | Safe correlated telemetry and dashboards | 007, 009, 026-028 |
| [035](035-compose-integration.md) | Complete health-gated local stack | 010, 015-034 |
| [036](036-pilot-hardening.md) | Recovery, adversarial, accessibility, and pilot E2E | 035 |

## Packet completion report

Each implementation handoff should end with:

- outcome and files changed;
- acceptance criteria satisfied;
- exact verification commands and results;
- unresolved risks, blockers, or design questions;
- confirmation that later packet scope was not implemented.
