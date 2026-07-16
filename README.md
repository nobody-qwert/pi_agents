# Deterministic Agent Orchestrator

Design workspace for a production-oriented, Dockerized multi-agent system built
around a fixed LangGraph control flow.

The application will provide:

- a visual graph of agent stages and permitted transitions;
- inspectable prompts, model settings, tools, schemas, and policy limits;
- a chat interface for submitting work and continuing conversations;
- live run progress with expandable agent, tool, validation, and approval steps;
- durable run state, resumable event streams, and OpenTelemetry observability;
- deterministic enforcement around agent proposals and state transitions.

This branch currently contains design documents, not a runnable application.

## Documents

- [PLAN.md](PLAN.md) defines the product model, role boundaries, work graph,
  deterministic runtime contract, and rollout strategy.
- [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md) defines the proposed technology
  stack, service architecture, schemas, APIs, UI behavior, Docker Compose
  topology, testing strategy, and implementation sequence.

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
