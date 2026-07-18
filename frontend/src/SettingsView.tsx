import { useQuery } from "@tanstack/react-query";
import { getJson, getServiceJson, readinessSchema, registrySchema } from "./api";

export function SettingsView() {
  const ready = useQuery({ queryKey: ["readiness"], queryFn: () => getServiceJson("/ready", readinessSchema), refetchInterval: 15_000, retry: false });
  const registry = useQuery({ queryKey: ["agents"], queryFn: () => getJson("/system/agents", registrySchema) });
  return <section className="page-stack">
    <header className="page-header"><div><p className="eyebrow">Runtime contract</p><h1>System</h1><p>Safe projections of dependency health and the pinned agent registry.</p></div></header>
    <div className="metric-grid"><article className="metric-card"><span>Control plane</span><strong className={ready.isSuccess ? "healthy" : "unhealthy"}>{ready.isPending ? "Checking" : ready.isSuccess ? "Ready" : "Unavailable"}</strong><small>{ready.data ? `Model ${ready.data.model_id}` : "Database or model dependency is not ready"}</small></article><article className="metric-card"><span>Registry</span><strong>{registry.data?.agents.length ?? "—"}</strong><small>pinned roles</small></article></div>
    {registry.isPending ? <div className="empty-state">Loading registry…</div> : registry.isError ? <div className="error-state" role="alert">The safe agent registry is unavailable.</div> : <><p className="hash-line">Registry hash <code>{registry.data.registry_hash}</code></p><div className="agent-list">{registry.data.agents.map((agent) => <article key={agent.agent_id}><div><h2>{agent.display_name}</h2><p>{agent.description}</p></div><dl className="compact-dl"><div><dt>Role</dt><dd>{agent.agent_id}</dd></div><div><dt>Output</dt><dd>{agent.output_schema}</dd></div><div><dt>Attempts</dt><dd>{agent.max_attempts}</dd></div></dl></article>)}</div></>}
  </section>;
}
