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

## Serial work-packet implementation

[`scripts/run-work-packets.sh`](scripts/run-work-packets.sh) implements the
numbered work packets sequentially. Every implementation, verification, and
repair phase starts a fresh ephemeral Codex context. The runner never executes
packets or agents in parallel.

The runner requires `codex`, `git`, `jq`, and `flock`, a clean working tree, and
an active Git branch with Git author name and email configured. It creates one
commit per independently verified packet with a `Work-Packet: NNN` trailer.
Those trailers make the process resumable: rerunning the command skips committed
packets and continues at the first unfinished packet.

Preview the sequence without making changes:

```bash
scripts/run-work-packets.sh --dry-run
```

Run all unfinished packets in the foreground:

```bash
scripts/run-work-packets.sh
```

To keep the process alive after closing the terminal, first ensure the runner
itself and all other intended changes are committed, then start it in the
background:

```bash
mkdir -p .work-packet-runs
nohup scripts/run-work-packets.sh >.work-packet-runs/runner.out 2>&1 &
```

Progress and complete Codex output are written beneath
`.work-packet-runs/<run-id>/`. The directory is ignored by Git. The runner stops
without starting the next packet when Codex fails, a result is malformed, an
acceptance check is blocked, verification changes repository files, the repair
limit is reached, or Git state changes unexpectedly. It does not push commits.

Use `--from NNN`, `--to NNN`, or `--max-repairs N` to bound a run. A stopped run
with source changes requires manual inspection and a clean working tree before
it can be restarted. Later runnable and end-to-end packets also require the
documented LM Studio, Docker, database, and VM infrastructure to be available;
missing infrastructure causes a safe stop rather than a skipped check.
The full sequence can take considerable time and Codex usage, and the host must
remain powered on with required local services running.
