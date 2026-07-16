# 035: Health-gated Docker Compose integration

## Objective

Assemble the implemented services into the documented local Docker Compose stack
with explicit configuration, health ordering, persistent volumes, and isolation.

## Context and references

- root `README.md` Intended local experience.
- `docs/design/TECHNICAL_DETAILS.md` Sections 11-12 and 16.

## Dependencies

- 010 and 015-034.

## In scope

- Production-oriented Dockerfiles and complete Compose service/network/volume map.
- `.env.example`, healthchecks, startup dependencies, non-root settings, KVM and
  read-only/writable mount boundaries.
- Database migrations/startup workflow and actionable readiness failures.
- Documented clean start, stop, rebuild, and local-data handling commands.

## Out of scope

- Feature implementation hidden in container startup scripts, production cloud
  deployment, and weakening isolation for convenience.

## Implementation constraints

- LM Studio stays external and readiness requires configured `qwen3.6-27b`.
- No guest/project writable host mount or Docker socket.
- Full documented demo includes observability as selected in the design defaults.

## Acceptance criteria

- `docker compose up --build` from a clean checkout reaches correct health/readiness
  when LM Studio and KVM prerequisites exist.
- Restart preserves durable records and recovery while ephemeral guests follow
  lifecycle policy.
- Network/mount/user inspection matches the security topology.

## Verification

- Run Compose config validation, image builds, health smoke tests, restart test,
  and isolation inspection; report unavailable infrastructure honestly.

## Handoff

- Report prerequisites, ports, volumes, and exact startup commands; stop before
  pilot hardening.
