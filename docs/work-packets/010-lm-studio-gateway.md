# 010: LM Studio model gateway and readiness

## Objective

Implement the required OpenAI-compatible LM Studio adapter and fail readiness
clearly unless the configured `qwen3.6-27b` model is available.

## Context and references

- root `README.md` Model runtime.
- `docs/design/TECHNICAL_DETAILS.md` Sections 3.4 and 11.1.

## Dependencies

- 001-002.

## In scope

- Internal `ModelGateway` protocol and LM Studio adapter.
- Typed request/response metadata, timeouts, cancellation, and policy-bounded
  retry classification.
- Model-list/readiness probe requiring the configured model ID.
- Redaction-safe diagnostics and adapter contract tests.

## Out of scope

- Prompts, agent parsing, runner stages, streaming UI tokens, and guest tools.
- Fake runtime providers or fallback to another model.

## Implementation constraints

- The API key/base URL remain server-side and logs omit secrets/content by
  default.
- Unit tests may mock transport at the gateway boundary; runnable integration
  tests use LM Studio when explicitly enabled.
- A missing/wrong model is unavailable, not silently substituted.

## Acceptance criteria

- Readiness distinguishes unreachable service, malformed response, absent model,
  and ready configured model.
- Timeout/cancellation and retryable versus terminal failures are typed.
- An opt-in integration check reaches the configured LM Studio endpoint.

## Verification

- Run gateway unit tests; run and report the opt-in LM Studio check when the
  required service is available.

## Handoff

- Report gateway configuration and readiness diagnostics; stop before agents.

