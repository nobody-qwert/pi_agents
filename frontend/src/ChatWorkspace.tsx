import { type FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJson, postJson, projectsSchema, runSchema, runsSchema, type Run } from "./api";
import { Timeline } from "./Timeline";
export function ChatWorkspace() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => getJson("/projects", projectsSchema) });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => getJson("/runs", runsSchema) });
  const [projectId, setProjectId] = useState(""); const [message, setMessage] = useState(""); const [activeRun, setActiveRun] = useState<Run>(); const [error, setError] = useState<string>();
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setError(undefined); try { const run = await postJson("/runs", { project_id: projectId, message }, runSchema); setActiveRun(run); setMessage(""); await runs.refetch(); } catch { setError("The run could not be submitted."); } }
  if (projects.isPending || runs.isPending) return <p>Loading workspace…</p>;
  if (projects.isError || runs.isError) return <p role="alert">Could not load the durable workspace state.</p>;
  return <section><h1>workspace</h1><form onSubmit={submit}><label>Workspace<select required value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select allowlisted workspace</option>{projects.data.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.display_name}</option>)}</select></label><label>Request<textarea required value={message} onChange={(event) => setMessage(event.target.value)} /></label><button disabled={!projectId || !message.trim()} type="submit">Start run</button></form>{error ? <p role="alert">{error}</p> : null}<section aria-label="Run history"><h2>Run history</h2><ul>{runs.data.map((run) => <li key={run.run_id}><button type="button" onClick={() => setActiveRun(run)}>{run.run_id}</button> — {run.status}</li>)}</ul></section>{activeRun ? <Timeline runId={activeRun.run_id} /> : null}</section>;
}
