# Deterministic Agent Orchestrator

Production-oriented, Dockerized multi-agent system built around a fixed
LangGraph control flow.

The application provides:

- a visual graph of agent stages and permitted transitions;
- inspectable prompts, model settings, tools, schemas, and policy limits;
- a chat interface for submitting work and continuing conversations;
- an allowlisted project picker that copies work into a disposable KVM guest;
- a live, interactive guest desktop with Chromium for web-oriented work;
- live run progress with expandable agent, tool, validation, and approval steps;
- durable run state, resumable event streams, and OpenTelemetry observability;
- guest-local Git checkpoints, rollback, and explicit version promotion back to
  a host Git repository;
- deterministic enforcement around agent proposals and state transitions.

Model-directed filesystem, shell, browser, build, and test operations run only
inside the disposable guest. The original host folder is never mounted writable
into that guest. A run starts from a copied snapshot and can affect the host
repository only through a reviewed, authenticated promotion command that creates
a new branch and commit without changing the user's current working tree.

## Model runtime

The required model runtime is LM Studio serving the locally hosted
`qwen3.6-27b` 27B model with `Q4_K_M` quantization through its OpenAI-compatible
API on port `1234`. The application will not provide or fall back to a fake
model.

Before starting the Compose stack:

1. Load the Qwen model in LM Studio.
2. Start the LM Studio local server.
3. Keep the server on host loopback; the Compose relay reaches it without
   exposing LM Studio to the LAN.
4. Confirm the served model ID and set `LM_STUDIO_MODEL_ID` when it differs from
   `qwen3.6-27b`.

Compose runs `lm-studio-relay` in the host network namespace solely to reach
`127.0.0.1:${LM_STUDIO_RELAY_UPSTREAM_PORT:-1234}`. It publishes no TCP ports;
instead, it exposes a mode `0660` Unix socket to the allowlisted
`inference-proxy` through a named volume. Other services and QEMU guests reach
LM Studio only through that proxy, so LM Studio does not need LAN serving or a
host firewall allowance.

The repository contains the backend, standalone runner, React operator UI,
QEMU/KVM manager, inference/egress proxies, authenticated desktop and preview
gateways, promotion manager, and local observability stack. Automated contract,
durability, recovery, and security-boundary tests pass; a real guest/model pilot
still requires the host prerequisites below.

## Documents

- [Design plan](docs/design/PLAN.md) defines the product model, role boundaries,
  work graph, deterministic runtime contract, and rollout strategy.
- [Technical details](docs/design/TECHNICAL_DETAILS.md) define the proposed
  technology stack, service architecture, schemas, APIs, UI behavior, Docker
  Compose topology, and testing strategy.
- [Implementation work packets](docs/work-packets/README.md) provide the
  dependency-ordered, bounded handoffs intended for developer-agent execution.
- [Implementation status](docs/IMPLEMENTATION_STATUS.md) records what is
  production-composed today and the remaining environment-dependent pilot gate.

## Branch intent

The original development harness remains on `master`. This feature branch is
reserved for the standalone LangGraph production application and does not carry
the legacy prompt tree, agent definitions, or orchestration image asset.

## Intended local experience

After generating the guest assets and starting LM Studio, startup is:

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.kvm.yml up --build
```

The web application should then be available at `http://localhost:3000`, with
the API health endpoint at `http://localhost:8000/health` and the local
observability dashboard at `http://localhost:3001`.

The local Compose definition exposes web on port 3000, API on port 8000, and
Grafana on port 3001. Create an allowlisted project directory and copy
the example settings before startup:

```bash
mkdir -p projects/example
cp .env.example .env
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.kvm.yml up --build
```

To use an existing project, set its host path and display name in `.env` before
starting Compose:

```text
PROJECT_PATH_HOST=/absolute/path/to/project
PROJECT_NAME=project-name
```

For the bounded API/web/PostgreSQL smoke profile, which tears down its
temporary containers on exit, run bash scripts/verify-compose-smoke.sh.

The full real-model/guest acceptance profile additionally requires a reachable
LM Studio server with `qwen3.6-27b`, `/dev/kvm`, a sealed image at
`vm/base/pi-base.qcow2`, and the generated material documented under `vm/ssh`.
Build the image with `scripts/build-vm-image.sh` after supplying a pinned vendor
image URL and SHA-256. Environment-dependent checks are reported as skipped when
their prerequisites are absent; they are not treated as a passing real-guest
end-to-end result.

## Work-packet implementation

Implement work packets manually, one at a time, by starting Codex with the
relevant packet and its referenced design sections. The dependency order and
handoff rules are documented in [the work-packet index](docs/work-packets/README.md).
