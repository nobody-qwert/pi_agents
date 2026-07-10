# Project Instructions

These rules apply to every pi process working in this repository.

## Working principles

- Prefer small, cohesive changes over broad rewrites.
- Locate the owning module before editing code.
- Read targeted files and ranges; do not load the whole repository into context.
- Preserve public behavior unless the task explicitly changes it.
- Do not weaken, delete, or bypass tests to make a change pass.
- Do not edit generated files, dependency lockfiles, or configuration unless they are explicitly in scope.
- Treat existing uncommitted changes as human-owned.

## Modularity

- Give each module one clear responsibility.
- Extend an existing appropriate module before creating a new abstraction.
- Keep policy separate from I/O and framework adapters.
- Put reusable logic behind a small explicit interface.
- Avoid hidden global state and circular dependencies.
- Add focused tests beside or near the behavior they verify.
- If a task crosses several responsibilities, stop and propose smaller task slices.

## Verification

- Discover the repository's real test, type-check, lint, and build commands from its manifests and documentation.
- Run the narrowest relevant check while developing.
- Run every acceptance command named in the delegated task before declaring success.
- Report commands exactly as run and whether they passed.
- Never claim a check passed if it was not executed.

## Failure discipline

- Do not retry an unchanged command after the same failure without a new evidence-based hypothesis.
- If the same normalized error survives two distinct fixes, stop and report `STUCK`.
- If required work lies outside the allowed paths or task contract, stop and report `BLOCKED_SCOPE`.
- Keep reports concise. Point to files and logs instead of pasting large output.

## Local subagent resource boundary

- All supervisor and worker inference uses the one loaded `lmstudio/qwen3.6-27b@q4_k_m` instance.
- Run subagents only in single, foreground, fresh-context mode.
- Never use parallel, background, async, scheduled, or nested subagent execution.
- Never start, load, unload, or reconfigure LM Studio from an agent.
