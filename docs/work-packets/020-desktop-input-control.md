# 020: Authenticated desktop and input ownership

## Objective

Expose the guest display through a short-lived authenticated channel and enforce
deterministic `AGENT`, `USER`, and `PAUSED` input ownership.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.7, 6.7, 10.5, and 13.

## Dependencies

- 003, 007, and 015.

## In scope

- noVNC-first guest display integration and desktop-gateway typed session tokens.
- Run/user binding, expiry, single-purpose WebSocket authorization, and cleanup.
- Input-owner transition service, automation pause handshake, and durable events.
- Clipboard, host transfer, microphone, camera, and profile sharing disabled.

## Out of scope

- React embedding, browser automation implementation, and general run auth design.

## Implementation constraints

- Raw VNC is never publicly exposed.
- USER ownership is accepted only after agent automation is confirmed paused.
- Token contents/logs reveal no reusable management credential.

## Acceptance criteria

- Valid sessions connect only to their run guest and expire/revoke correctly.
- Unauthorized/cross-run/replayed tokens and conflicting ownership changes fail.
- Take/return control produces ordered events and never permits simultaneous
  user/agent input.

## Verification

- Run token, WebSocket authorization, expiry, and ownership-race tests.

## Handoff

- Report session and ownership protocols; stop before frontend work.

