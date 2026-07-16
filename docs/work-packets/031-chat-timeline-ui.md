# 031: Chat and durable live run timeline

## Objective

Let users submit messages/runs and follow a reconnectable, expandable,
operator-readable timeline driven by durable events.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 2, 8.2, 10.2, and 14.3.

## Dependencies

- 027-029.

## In scope

- Conversation/message views and run-start interaction after workspace readiness.
- Typed SSE client with sequence persistence, reconnect/backoff, and deduplication.
- Collapsed pipeline summary and chronological event upsert by stable identity.
- Lazy typed details for agent/tool/validation/transition/artifact/error events.
- Searchable run history with reopen/resume into its durable conversation and
  timeline state.

## Out of scope

- Project selection implementation, desktop/checkpoints, approvals, promotion,
  and trace dashboard configuration.

## Implementation constraints

- Started-to-terminal updates replace the logical row rather than append noise.
- Refresh resumes from durable state/cursor, not in-memory transcript history.
- Never display or request raw chain-of-thought.

## Acceptance criteria

- Submit returns durable IDs and the timeline follows the correct run.
- Disconnect/reload/reconnect loses and duplicates no fixture events.
- Detail views handle loading, authorization, redaction, and errors accessibly.
- Run-history filtering and reopening use backend state without reconstructing
  runs from browser-local data.

## Verification

- Run SSE client, timeline upsert, reload/reconnect, and accessibility tests.

## Handoff

- Report client cursor storage and event reducer behavior; stop before workspace UI.
