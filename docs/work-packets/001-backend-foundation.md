# 001: Backend foundation and quality gates

## Objective

Create the minimal Python backend package and repeatable local quality commands
that later backend packets can extend.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.1, 11.1, and 12.
- Root `AGENTS.md`.

## Dependencies

- None.

## In scope

- `backend/pyproject.toml`, importable `orchestrator` package, and test layout.
- Python 3.12+ configuration for formatting/linting, typing, and pytest.
- A minimal application settings loader with strict environment parsing.
- Documented backend install, test, type-check, and lint commands.

## Out of scope

- HTTP routes, database access, LangGraph, model calls, and Docker images.
- Domain records beyond settings needed to prove the package works.

## Implementation constraints

- Keep dependencies minimal and version-constrained.
- Settings must not embed secrets or silently choose a fake model/provider.
- Do not create placeholder production abstractions that later packets own.

## Acceptance criteria

- The package imports under the supported Python version.
- A settings unit test proves required values and invalid values are handled
  deliberately.
- The documented test, type-check, and lint commands pass from a clean setup.

## Verification

- Run the new narrow tests and every backend quality command introduced here.
- Record exact commands for all later packet authors.

## Handoff

- Report the chosen package/tool commands and stop before defining domain state.

