# Deterministic Agent Orchestrator

Design workspace for a production-oriented, Dockerized multi-agent system built
around a fixed LangGraph control flow.

The application will provide:

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
3. Ensure the server is reachable from Docker, not only from host loopback.
4. Confirm the served model ID and set `LM_STUDIO_MODEL_ID` when it differs from
   `qwen3.6-27b`.

The main application on this branch is still a design target, not a runnable
implementation. `sample_disposable_vm/` is a reference VM-manager prototype;
it is not yet integrated into the production Compose topology described here.

## Documents

- [Design plan](docs/design/PLAN.md) defines the product model, role boundaries,
  work graph, deterministic runtime contract, and rollout strategy.
- [Technical details](docs/design/TECHNICAL_DETAILS.md) define the proposed
  technology stack, service architecture, schemas, APIs, UI behavior, Docker
  Compose topology, and testing strategy.
- [Implementation work packets](docs/work-packets/README.md) provide the
  dependency-ordered, bounded handoffs intended for developer-agent execution.

## Branch intent

The original development harness remains on `master`. This feature branch is
reserved for the standalone LangGraph production application and does not carry
the legacy prompt tree, agent definitions, or orchestration image asset.

## Intended local experience

After the first implementation milestone, startup should be:

```bash
cp .env.example .env
docker compose up --build
```

The web application should then be available at `http://localhost:3000`, with
the API health endpoint at `http://localhost:8000/health` and the local
observability dashboard at `http://localhost:3001`.

Exact commands and ports remain design targets until the Dockerized vertical
slice is implemented and verified.
