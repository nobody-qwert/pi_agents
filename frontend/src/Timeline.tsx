import { Fragment, useEffect, useState } from "react";
import { getJson, type EventDetail, type RunEvent, eventDetailSchema } from "./api";
import { RunEventClient } from "./sse";
import { upsertTimeline, type TimelineRow } from "./timeline";

function EventDetailView({ event }: { event: RunEvent }) {
  const [detail, setDetail] = useState<EventDetail>();
  const [error, setError] = useState<string>();
  useEffect(() => { setDetail(undefined); setError(undefined); if (!event.detail_ref) return; void getJson(event.detail_ref, eventDetailSchema).then(setDetail).catch(() => setError("Details are unavailable or not authorized.")); }, [event.detail_ref]);
  if (!event.detail_ref) return <p className="muted">No additional safe detail is available.</p>;
  if (error) return <p role="alert">{error}</p>;
  if (!detail) return <p className="muted">Loading safe detail…</p>;
  const traceUrl = event.trace_id ? `${window.location.protocol}//${window.location.hostname}:3001/explore?left=${encodeURIComponent(JSON.stringify(["now-1h", "now", "Tempo", { query: event.trace_id }]))}` : undefined;
  return <div className="event-detail"><p>{detail.summary}</p>{traceUrl ? <p><a href={traceUrl} target="_blank" rel="noreferrer">Open correlated trace {event.trace_id} in Grafana</a></p> : null}<dl>{detail.fields.map((field) => <Fragment key={field.label}><dt>{field.label}</dt><dd>{String(field.value ?? "—")}</dd></Fragment>)}</dl></div>;
}

export function Timeline({ runId }: { runId: string }) {
  const [rows, setRows] = useState<TimelineRow[]>([]);
  const [error, setError] = useState<string>();
  useEffect(() => { setRows([]); setError(undefined); const client = new RunEventClient(runId, (event) => setRows((current) => upsertTimeline(current, event)), setError); client.start(); return () => client.stop(); }, [runId]);
  return <section className="timeline" aria-label={`Run ${runId} timeline`}><div className="section-heading"><div><p className="eyebrow">Event stream</p><h2>Timeline</h2></div><span className="count-badge">{rows.length}</span></div>{error ? <p className="stream-warning" role="status">Live connection is retrying: {error}</p> : null}{rows.length === 0 ? <div className="empty-state">Waiting for the first durable event…</div> : <ol>{rows.map(({ key, event }) => <li key={key}><span className={`event-marker status-${event.status ?? "pending"}`} /><details><summary><span><strong>{event.summary ?? event.type}</strong><small>{event.stage ?? event.type} · sequence {event.sequence}</small></span><time>{event.occurred_at ? new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(new Date(event.occurred_at)) : ""}</time></summary><EventDetailView event={event} /></details></li>)}</ol>}</section>;
}
