import type { RunEvent } from "./api";
export type TimelineRow = { key: string; event: RunEvent };
const lifecycle = new Set(["agent.started", "agent.completed", "agent.failed", "tool.requested", "tool.started", "tool.completed", "tool.failed", "validation.started", "validation.accepted", "validation.rejected"]);
export function timelineKey(event: RunEvent): string { if (lifecycle.has(event.type)) return event.type.split(".")[0] + ":" + (event.attempt_id ?? event.work_node_id ?? event.stage ?? event.event_id); return event.event_id; }
export function upsertTimeline(rows: readonly TimelineRow[], event: RunEvent): TimelineRow[] { const key = timelineKey(event); const index = rows.findIndex((row) => row.key === key); const next = { key, event }; if (index < 0) return [...rows, next].sort((left, right) => left.event.sequence - right.event.sequence); return rows.map((row, current) => current === index ? next : row); }
