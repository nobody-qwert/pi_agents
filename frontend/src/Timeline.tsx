import { useEffect, useState } from "react";
import { getJson, type EventDetail, type RunEvent, eventDetailSchema } from "./api";
import { RunEventClient } from "./sse";
import { upsertTimeline, type TimelineRow } from "./timeline";
function EventDetailView({ event }: { event: RunEvent }) {
  const [detail, setDetail] = useState<EventDetail>(); const [error, setError] = useState<string>();
  useEffect(() => { if (!event.detail_ref) return; void getJson(event.detail_ref, eventDetailSchema).then(setDetail).catch(() => setError("Details are unavailable or not authorized.")); }, [event.detail_ref]);
  if (!event.detail_ref) return <p>No additional safe detail is available.</p>;
  if (error) return <p role="alert">{error}</p>;
  if (!detail) return <p>Loading detail…</p>;
  return <dl><dt>{detail.category}</dt><dd>{detail.summary}</dd>{detail.fields.map((field) => <><dt key={field.label + "-label"}>{field.label}</dt><dd key={field.label + "-value"}>{String(field.value)}</dd></>)}</dl>;
}
export function Timeline({ runId }: { runId: string }) {
  const [rows, setRows] = useState<TimelineRow[]>([]); const [error, setError] = useState<string>();
  useEffect(() => { setRows([]); const client = new RunEventClient(runId, (event) => setRows((current) => upsertTimeline(current, event)), setError); client.start(); return () => client.stop(); }, [runId]);
  return <section aria-label={"Run " + runId + " timeline"}><h2>Run timeline</h2>{error ? <p role="alert">{error}</p> : null}<p>{rows.length} durable events</p><ol>{rows.map(({ key, event }) => <li key={key}><details><summary>{event.status ?? event.type}: {event.summary ?? event.type} <small>#{event.sequence}</small></summary><EventDetailView event={event} /></details></li>)}</ol></section>;
}
