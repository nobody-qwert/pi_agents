import { runEventSchema, type RunEvent } from "./api";

const cursorKey = (runId: string) => "orchestrator.run." + runId + ".sequence";
export function storedCursor(runId: string): number { const value = Number(localStorage.getItem(cursorKey(runId)) ?? "0"); return Number.isInteger(value) && value >= 0 ? value : 0; }
function saveCursor(runId: string, sequence: number) { localStorage.setItem(cursorKey(runId), String(sequence)); }
export type SseFrame = { id?: string; event?: string; data?: string };
export function parseSseFrames(source: string): SseFrame[] {
  return source.split("\n\n").flatMap((block) => {
    const frame: SseFrame = {};
    for (const line of block.split("\n")) { const delimiter = line.indexOf(":"); if (delimiter > 0) { const key = line.slice(0, delimiter); const value = line.slice(delimiter + 1).trimStart(); if (key === "id") frame.id = value; if (key === "event") frame.event = value; if (key === "data") frame.data = value; } }
    return frame.data ? [frame] : [];
  });
}
export class RunEventClient {
  private stopped = false;
  private retry = 250;
  private cursor = 0;
  constructor(private readonly runId: string, private readonly onEvent: (event: RunEvent) => void, private readonly onError: (message: string) => void = () => {}) {}
  start() { this.stopped = false; void this.connect(); }
  stop() { this.stopped = true; }
  private async connect(): Promise<void> {
    try {
      const response = await fetch("/api/v1/runs/" + this.runId + "/events", { headers: { Accept: "text/event-stream", "X-Dev-User": "user_local", "Last-Event-ID": String(this.cursor) } });
      if (!response.ok || !response.body) throw new Error("event_stream_unavailable");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ""; let terminal = false;
      while (!this.stopped) { const chunk = await reader.read(); if (chunk.done) break; buffer += decoder.decode(chunk.value, { stream: true }); const boundary = buffer.lastIndexOf("\n\n"); if (boundary < 0) continue; for (const frame of parseSseFrames(buffer.slice(0, boundary))) { const event = runEventSchema.parse({ ...JSON.parse(frame.data ?? "{}"), type: frame.event ?? "unknown", sequence: Number(frame.id) }); if (event.sequence > this.cursor) { this.cursor = event.sequence; saveCursor(this.runId, event.sequence); this.onEvent(event); } terminal ||= ["run.completed", "run.failed", "run.blocked"].includes(event.type); } buffer = buffer.slice(boundary + 2); }
      if (terminal) return;
    } catch (error) { this.onError(error instanceof Error ? error.message : "event_stream_unavailable"); }
    if (!this.stopped) { await new Promise((resolve) => window.setTimeout(resolve, this.retry)); this.retry = Math.min(this.retry * 2, 8_000); await this.connect(); }
  }
}
