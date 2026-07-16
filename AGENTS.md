# Project Instructions

## Working principles

- Prefer small, cohesive changes over broad rewrites.
- Locate the owning module before editing code.
- Preserve public behavior unless the task explicitly changes it.
- Treat existing uncommitted changes as human-owned.
- Do not edit generated files or dependency lockfiles unless explicitly in scope.

## Architecture

- Keep the LangGraph control graph fixed and code-defined.
- Treat all model output as untrusted proposals that require schema and policy validation.
- Keep domain policy separate from transport, persistence, model, tool, and UI adapters.
- Make authoritative state transitions deterministic, idempotent, and auditable.
- Keep API, runner, frontend, persistence, telemetry, and domain responsibilities explicit.
- Do not let an agent approve its own work or broaden its authority.

## Modularity

- Give each module one clear responsibility.
- Put reusable logic behind small explicit interfaces.
- Avoid hidden global state and circular dependencies.
- Add focused tests beside or near the behavior they verify.
- Keep prompts and agent configuration versioned separately from runtime code.

## Verification

- Discover and run the repository's actual test, type-check, lint, and build commands.
- Run the narrowest relevant check during development.
- Run all acceptance commands named by the task before declaring success.
- Never weaken or bypass tests to make a change pass.
- Report commands exactly as run and do not claim unexecuted checks passed.

## Failure discipline

- Do not repeat an unchanged failing experiment.
- Stop and report a concrete blocker when required authority, infrastructure, or scope is missing.
- Preserve concise failure fingerprints and decisive evidence rather than full transcripts.
