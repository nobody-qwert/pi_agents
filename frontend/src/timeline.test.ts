import { describe, expect, it } from "vitest";
import type { RunEvent } from "./api";
import { parseSseFrames } from "./sse";
import { upsertTimeline } from "./timeline";
const event = (event_id: string, sequence: number, type: string, attempt_id = "attempt_1"): RunEvent => ({ event_id, sequence, type, attempt_id, status: type.endsWith("completed") ? "completed" : "started" });
describe("timeline", () => {
  it("parses durable SSE frames", () => expect(parseSseFrames("id: 4\nevent: agent.completed\ndata: {\"event_id\":\"evt_4\"}\n\n")).toEqual([{ id: "4", event: "agent.completed", data: "{\"event_id\":\"evt_4\"}" }]));
  it("replaces a lifecycle row", () => { const started = event("evt_started", 1, "agent.started"); const completed = event("evt_completed", 2, "agent.completed"); const rows = upsertTimeline(upsertTimeline([], started), completed); expect(rows).toHaveLength(1); expect(rows[0]?.event.event_id).toBe("evt_completed"); });
});
