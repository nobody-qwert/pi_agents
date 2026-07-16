# 024: Guest execution and independent local verification

## Objective

Process an approved ready leaf through immutable dispatch, scoped guest execution,
and an independent local-verification decision with evidence/checkpoint handling.

## Context and references

- `docs/design/PLAN.md` Sections 5-6 and 7.4.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.6, 4, and 6.

## Dependencies

- 012, 016-019, and 023.

## In scope

- Fixed `DISPATCH -> EXECUTE -> LOCAL_VERIFY` stage application services.
- Executor invocation with packet-pinned role/tool policy and bounded attempts.
- Worker report/artifact intake and verifier read-only context/invocation.
- Deterministic evidence acceptance, locally-verified transition, accepted
  checkpoint request, or issue/blocked routing.

## Out of scope

- Integration/outcome verification, redesign implementation, browser UI, and host
  promotion decisions.

## Implementation constraints

- Executor cannot verify itself; verifier tools are read-only plus bounded checks.
- A worker claim or passing command alone is not authoritative verification.
- Provisional guest changes become accepted only through validated artifact,
  evidence, transition, and checkpoint services.

## Acceptance criteria

- A valid leaf changes only permitted guest touch points and reaches locally
  verified with linked evidence and checkpoint.
- Failed checks, protected changes, stale packet/design, timeout, and scope gaps
  route to an issue without false verification.
- Retry/resume is bounded and does not repeat accepted mutations/events.

## Verification

- Run delivery-stage tests and a real guest-runtime integration scenario.

## Handoff

- Report attempt/evidence/checkpoint semantics; stop before integration stages.

