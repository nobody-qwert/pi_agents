# Implementation status

Last verified: 2026-07-17

This document maps the design to executable production composition. A module is
not called complete merely because a unit test exists.

## Implemented vertical slice

The trusted control plane now provides:

- PostgreSQL-owned conversations, messages, commands, ordered events, attempts,
  work graphs, evidence, issues, artifacts, approvals, workspaces, checkpoints,
  desktop ownership, and promotion records with replay-safe mutations;
- a standalone fenced runner with PostgreSQL LangGraph recovery and the complete
  fixed delivery/outcome loop, including bounded repair and change-request paths;
- registry-pinned structured Pi RPC attempts for every role, deterministic
  validation, and validated guest diff copy-out into immutable artifact storage;
- a QEMU/KVM manager with sanitized copy-in, a separate guest Git baseline,
  checkpoint/rollback, protected paths, typed role tools, Chromium tooling,
  localhost SSH application previews, and no model-directed host shell;
- fail-closed inference and egress proxies with exact inference routes,
  destination policy, SSRF/private-network denial, per-run byte budgets, and
  durable egress audit decisions;
- authenticated noVNC sessions with expiring run/user grants and serialized
  `AGENT`/`PAUSED`/`USER` input ownership;
- immutable promotion previews, complete patch download, source-baseline recheck,
  isolated worktree branch/commit/tag creation, and versioned fallback review
  repositories for dirty, stale, non-Git, conflicting, or failed-check cases;
- the complete typed HTTP surface for projects, conversations, runs, replayable
  events/details, work graphs, artifacts, approvals, workspace state,
  checkpoints, desktop, application previews, and promotion;
- a dynamic React UI for durable chat/history, fixed and work graphs, timeline
  details, copy policy, desktop reconnect/control, application previews,
  checkpoints/rollback, authority decisions, full promotion evidence, and result
  download;
- OpenTelemetry export plus provisioned Prometheus, Loki, Tempo, and Grafana
  services/dashboard, with content capture disabled and bounded safe attributes.

## Work-packet status

| Packets | Status | Evidence / qualification |
| --- | --- | --- |
| 001-014 | Implemented | Domain, registry, graph, policy, PostgreSQL, event, artifact, model, invocation, dispatch, triage, and project boundaries are composed and tested. |
| 015-022 | Implemented | Durable VM/workspace/checkpoint/desktop/promotion services and typed production adapters are composed. Real KVM acceptance still requires the host prerequisites below. |
| 023-025 | Implemented | Approved planning, guest execution, local verification, integration, outcome verification, evidence, repair, and change-request loops are wired into the production runner. |
| 026-028 | Implemented | Owned command/query APIs, conversation continuation, immutable artifact reads/downloads, ordered SSE replay, safe detail, and readiness are exposed. |
| 029-033 | Implemented | Backend-defined graph, chat/timeline, run history, workspace/desktop/preview/checkpoint flows, approvals, and promotion flows are mounted and build successfully. |
| 034 | Implemented for the local stack | API/runner telemetry, OTLP composition, metrics/logs/traces stores, dashboard provisioning, and safe-attribute tests exist. Production alert routing/retention remains deployment policy. |
| 035 | Implemented | PostgreSQL, API, runner, VM manager, desktop/promotion gateways, inference/egress proxies, web, and optional observability topology build and validate. KVM is supplied through `docker-compose.kvm.yml`. |
| 036 | Automated gates pass; hardware pilot pending | Recovery, duplicate delivery, stale/expired approval, rollback, promotion conflicts, proxy policy, budgets, and adversarial boundaries are covered. A real guest/model pilot cannot run on the current host. |

## External acceptance prerequisites

The remaining first-milestone proof is environmental rather than an unimplemented
code path. This workstation currently has no usable `/dev/kvm`, VM base image,
guest SSH private key/known-hosts file, or reachable configured LM Studio
upstream. The stack therefore fails closed: the inference proxy remains
unhealthy and VM-manager preflight refuses provisioning.

To run the final pilot on a capable host:

1. Build the documented guest image and install the generated SSH assets under
   `vm/base` and `vm/ssh`.
2. Make `/dev/kvm` available and set `KVM_GID` if required.
3. Start the configured LM Studio model and verify its OpenAI-compatible endpoint.
4. Start with `docker compose -f docker-compose.yml -f docker-compose.kvm.yml up --build`.
5. Execute a complete UI run, exercise desktop/preview/rollback, and promote the
   reviewed checkpoint while inspecting the correlated Grafana trace.

## Current verification

- Backend: `312 passed, 1 skipped`; the only skip is the explicitly opt-in live
  LM Studio contract.
- Backend quality: Ruff and strict mypy pass.
- Frontend: Vitest, ESLint, TypeScript, and the production Vite build pass.
- Packaging: all application images build and `docker compose config --quiet`
  passes. Startup reaches the expected fail-closed inference health gate when
  the configured upstream is absent.
