import { type FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { conversationMessageResultSchema, conversationSchema, getJson, postJson, projectsSchema, runSchema, runsSchema, type Run } from "./api";
import { Timeline } from "./Timeline";
import { WorkspaceControls } from "./WorkspaceControls";
import { PromotionControls } from "./PromotionControls";

function shortId(value: string) { return value.length > 20 ? `${value.slice(0, 11)}…${value.slice(-6)}` : value; }

export function ChatWorkspace() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => getJson("/projects", projectsSchema) });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => getJson("/runs", runsSchema) });
  const [params, setParams] = useSearchParams();
  const [projectId, setProjectId] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const [panel, setPanel] = useState<"timeline" | "workspace" | "promotion">("timeline");
  const activeRun = useMemo(() => runs.data?.find((run) => run.run_id === params.get("run")), [params, runs.data]);
  const conversationId = activeRun?.conversation_id;
  const conversation = useQuery({ queryKey: ["conversation", conversationId], queryFn: () => getJson("/conversations/" + conversationId, conversationSchema), enabled: Boolean(conversationId) });
  function selectRun(run: Run) { setParams({ run: run.run_id }); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined); setSubmitting(true);
    try {
      if (conversationId) {
        const result = await postJson("/conversations/" + conversationId + "/messages", { content: message, project_id: projectId }, conversationMessageResultSchema);
        setMessage(""); await Promise.all([runs.refetch(), conversation.refetch()]); if (result.run_id) setParams({ run: result.run_id });
      } else {
        const run = await postJson("/runs", { project_id: projectId, message }, runSchema); setMessage(""); await runs.refetch(); selectRun(run);
      }
    }
    catch { setError("The run could not be queued. Confirm that PostgreSQL and the configured model are ready."); }
    finally { setSubmitting(false); }
  }
  async function addContext() {
    if (!conversationId || !message.trim()) return; setError(undefined); setSubmitting(true);
    try { await postJson("/conversations/" + conversationId + "/messages", { content: message }, conversationMessageResultSchema); setMessage(""); await conversation.refetch(); }
    catch { setError("The conversation message could not be recorded."); }
    finally { setSubmitting(false); }
  }
  async function cancel(run: Run) {
    setError(undefined);
    try { await postJson(`/runs/${run.run_id}/cancel`, {}, runSchema); await runs.refetch(); }
    catch { setError("Cancellation was not recorded; the run may already be terminal."); }
  }
  if (projects.isPending || runs.isPending) return <div className="empty-state">Loading durable workspace…</div>;
  if (projects.isError || runs.isError) return <div className="error-state" role="alert">Could not load the durable workspace state.</div>;
  const selectedProject = projects.data.projects.find((project) => project.project_id === projectId);
  return <section className="workspace-page">
    <div className="workspace-main page-stack">
      <header className="page-header"><div><p className="eyebrow">Outcome control</p><h1>Workspace</h1><p>Queue a request against an allowlisted project, then follow its durable control-plane events.</p></div>{activeRun ? <span className={`status-pill status-${activeRun.status}`}>{activeRun.status}</span> : null}</header>
      <form className="composer" onSubmit={submit}><div className="composer-row"><label><span>Project</span><select required value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Select an allowlisted project</option>{projects.data.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.display_name}</option>)}</select></label>{selectedProject ? <p className="project-meta">{selectedProject.file_count} files · {selectedProject.included_bytes.toLocaleString()} bytes · {selectedProject.git_dirty === true ? "dirty Git tree" : selectedProject.git_head ? "clean Git baseline" : "non-Git source"}</p> : null}</div><label><span>{conversationId ? "Continue this conversation" : "Requested outcome"}</span><textarea required maxLength={16_384} rows={5} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Describe the complete outcome and constraints…" /></label><div className="composer-actions"><small>{message.length.toLocaleString()} / 16,384</small>{conversationId ? <button disabled={!message.trim() || submitting} type="button" onClick={() => void addContext()}>Add context only</button> : null}<button className="primary-button" disabled={!projectId || !message.trim() || submitting} type="submit">{submitting ? "Queueing…" : conversationId ? "Start follow-up run" : "Start durable run"}</button></div></form>
      {error ? <div className="error-state" role="alert">{error}</div> : null}
      {activeRun ? <section className="active-run"><div className="run-heading"><div><p className="eyebrow">{activeRun.current_gate}</p><h2>{activeRun.message}</h2><p className="mono">{activeRun.run_id}</p></div>{!["completed", "failed", "blocked", "cancelled"].includes(activeRun.status) ? <button className="danger-button" type="button" onClick={() => void cancel(activeRun)}>Request cancellation</button> : null}</div>{conversation.data ? <section aria-label="Durable conversation"><h3>Conversation</h3><ol className="conversation-messages">{conversation.data.messages.map((item) => <li key={item.message_id}><span>{item.role}</span><p>{item.content}</p></li>)}</ol></section> : conversation.isError ? <p role="alert">Conversation history is unavailable.</p> : null}<div className="graph-toolbar" role="tablist" aria-label="Run detail"><button role="tab" aria-selected={panel === "timeline"} type="button" onClick={() => setPanel("timeline")}>Timeline</button><button role="tab" aria-selected={panel === "workspace"} type="button" onClick={() => setPanel("workspace")}>Guest workspace</button><button role="tab" aria-selected={panel === "promotion"} type="button" onClick={() => setPanel("promotion")}>Approvals and promotion</button></div>{panel === "timeline" ? <Timeline runId={activeRun.run_id} /> : panel === "workspace" ? <WorkspaceControls runId={activeRun.run_id} /> : <PromotionControls runId={activeRun.run_id} />}</section> : <div className="empty-state hero-empty"><strong>No run selected</strong><span>Start a new outcome or reopen one from durable history.</span></div>}
    </div>
    <aside className="run-rail" aria-label="Recent runs"><div className="rail-heading"><div><p className="eyebrow">History</p><h2>Recent runs</h2></div><span>{runs.data.length}</span></div>{runs.data.length === 0 ? <p className="muted">No runs yet.</p> : <ol>{runs.data.slice(0, 12).map((run) => <li key={run.run_id}><button className={activeRun?.run_id === run.run_id ? "selected" : ""} type="button" onClick={() => selectRun(run)}><span><strong>{run.message}</strong><small>{shortId(run.run_id)}</small></span><span className={`status-dot status-${run.status}`} title={run.status} /></button></li>)}</ol>}</aside>
  </section>;
}
