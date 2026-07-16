# 019: Controlled egress and guest browser runtime

## Objective

Provide policy-controlled guest web access and typed Chromium/Playwright tools
while denying host, private, metadata, reserved, and management destinations.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 3.6-3.7, 11, and 13.

## Dependencies

- 015 and 017.

## In scope

- HTTP/HTTPS egress policy and inference-only LM Studio route allowlist.
- DNS resolution/rebinding checks, destination audit metadata, and transfer
  budgets.
- Sealed guest Chromium/Playwright setup and typed navigation/snapshot/click/
  input/screenshot/console/network operations.
- Browser tool role policy and bounded outputs/artifacts.

## Out of scope

- Interactive VNC desktop, frontend embedding, general TCP egress, or host browser
  profile/clipboard/file sharing.

## Implementation constraints

- Validate destination before and after resolution/redirect; default deny.
- Inference proxy permits only required readiness/inference routes, never LM
  Studio management.
- Browser operations remain in the run guest and use typed requests.

## Acceptance criteria

- Public allowlisted test destinations work under configured budgets.
- Loopback, host, RFC1918, link-local, metadata, reserved, rebinding, and forbidden
  inference routes are blocked and recorded.
- Browser tools complete a bounded fixture flow without host access.

## Verification

- Run policy/adversarial tests plus the guest browser integration profile.

## Handoff

- Report egress defaults and browser contracts; stop before interactive desktop.

