# 030: Control/work graph visualization and inspector

## Objective

Render the backend-defined fixed graph and dynamic approved work graph with
deterministic layout, accessible preview, inspection, and live run overlays.

## Context and references

- `docs/design/TECHNICAL_DETAILS.md` Sections 5.1, 10.3, and 14.3.

## Dependencies

- 004 and 029.

## In scope

- React Flow canvas and ELK.js deterministic layout.
- Static-control versus work-graph toggle and backend data adapters.
- Hover/focus card, click/keyboard inspector, redacted config/prompt/schema view.
- Active/completed/failed/approval/blocked/traversed overlay states and stage
  filtering callback.

## Out of scope

- Editing topology/config, timeline implementation, and graph data invented by
  the frontend.

## Implementation constraints

- Backend registry is the sole topology source.
- Hover is not the only access path; keyboard/focus and reduced-motion behavior
  are supported.
- Large graph updates preserve stable layout/selection where possible.

## Acceptance criteria

- Fixture graph renders exact permitted nodes/edges in deterministic positions.
- Preview/inspector exposes only safe fields and is fully keyboard reachable.
- Run events update styles without duplicating or mutating topology.

## Verification

- Run component/integration/accessibility tests and frontend quality/build checks.

## Handoff

- Report graph projection assumptions; stop before chat/timeline.
