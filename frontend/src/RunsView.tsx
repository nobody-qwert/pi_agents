import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJson, runsSchema } from "./api";

function formatTime(value: string | null) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Unknown time";
}

export function RunsView() {
  const query = useQuery({ queryKey: ["runs"], queryFn: () => getJson("/runs", runsSchema) });
  const [filter, setFilter] = useState("");
  const runs = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase();
    if (!query.data || !needle) return query.data ?? [];
    return query.data.filter((run) => [run.run_id, run.message, run.status, run.current_gate].some((value) => value.toLocaleLowerCase().includes(needle)));
  }, [filter, query.data]);
  return <section className="page-stack">
    <header className="page-header"><div><p className="eyebrow">Durable history</p><h1>Runs</h1><p>Reopen any run from PostgreSQL and resume its event timeline.</p></div><span className="count-badge">{query.data?.length ?? 0}</span></header>
    <label className="search-field"><span>Search runs</span><input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Request, stage, status, or run ID" /></label>
    {query.isPending ? <div className="empty-state">Loading durable runs…</div> : query.isError ? <div className="error-state" role="alert">Run history is unavailable.</div> : runs.length === 0 ? <div className="empty-state">No matching runs.</div> : <div className="run-grid">{runs.map((run) => <article className="run-card" key={run.run_id}>
      <div className="run-card-top"><span className={`status-pill status-${run.status}`}>{run.status}</span><time>{formatTime(run.created_at)}</time></div>
      <h2>{run.message}</h2><p className="mono truncate">{run.run_id}</p>
      <dl className="compact-dl"><div><dt>Gate</dt><dd>{run.current_gate}</dd></div><div><dt>Project</dt><dd>{run.project_id}</dd></div></dl>
      <Link className="primary-link" to={`/workspace?run=${encodeURIComponent(run.run_id)}`}>Open run</Link>
    </article>)}</div>}
  </section>;
}
