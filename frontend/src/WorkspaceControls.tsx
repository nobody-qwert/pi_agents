import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJson, postJson, checkpointsSchema, desktopSchema, workspaceSchema, type Workspace } from "./api";

function CopyPolicy({ workspace }: { workspace: Workspace }) { return <section aria-label="Copy policy"><h2>Copy policy</h2><p>Source fingerprint: <code>{workspace.source_fingerprint}</code></p><p>Excluded: {workspace.excluded_paths.join(", ") || "None"}</p><p>Protected: {workspace.protected_paths.join(", ") || "None"}</p></section>; }
function Checkpoints({ runId }: { runId: string }) {
  const query = useQuery({ queryKey: ["checkpoints", runId], queryFn: () => getJson("/runs/" + runId + "/workspace/checkpoints", checkpointsSchema) }); const [target, setTarget] = useState<string>(); const [error, setError] = useState<string>();
  async function rollback() { if (!target) return; setError(undefined); try { await postJson("/runs/" + runId + "/workspace/checkpoints/" + target + "/rollback", { confirm: true }, checkpointsSchema); await query.refetch(); setTarget(undefined); } catch { setError("Rollback was not applied. Refresh the checkpoint state before trying again."); } }
  if (query.isPending) return <p>Loading checkpoints…</p>; if (query.isError) return <p role="alert">Checkpoint state is unavailable.</p>;
  return <section aria-label="Checkpoints"><h2>Checkpoints</h2><ul>{query.data.checkpoints.map((checkpoint) => <li key={checkpoint.checkpoint_id}><label><input type="radio" checked={target === checkpoint.checkpoint_id} onChange={() => setTarget(checkpoint.checkpoint_id)} name="rollback-target" />{checkpoint.kind} — <code>{checkpoint.commit_hash}</code></label></li>)}</ul>{target ? <><p role="status">Rollback will restore the selected guest checkpoint. Earlier authoritative history remains preserved.</p><button type="button" onClick={() => void rollback()}>Confirm rollback</button></> : null}{error ? <p role="alert">{error}</p> : null}</section>;
}
function Desktop({ runId }: { runId: string }) {
  const query = useQuery({ queryKey: ["desktop", runId], queryFn: () => postJson("/runs/" + runId + "/workspace/desktop-sessions", {}, desktopSchema) }); const [error, setError] = useState<string>();
  async function changeOwner(owner: "USER" | "AGENT") { setError(undefined); try { await postJson("/runs/" + runId + "/workspace/input-owner", { owner }, desktopSchema); await query.refetch(); } catch { setError("Input ownership did not change. The displayed state remains authoritative."); } }
  if (query.isPending) return <p>Connecting guest desktop…</p>; if (query.isError) return <p role="alert">Desktop is unavailable or its session expired.</p>;
  const desktop = query.data; return <section aria-label="Guest desktop"><h2>Guest desktop</h2><p>Input owner: {desktop.input_owner}; session expires {desktop.expires_at}</p><button type="button" disabled={desktop.input_owner === "USER"} onClick={() => void changeOwner("USER")}>Take control (pause automation)</button><button type="button" disabled={desktop.input_owner !== "USER"} onClick={() => void changeOwner("AGENT")}>Return to agent</button>{error ? <p role="alert">{error}</p> : null}<iframe title="Authenticated guest desktop" src={desktop.websocket_url} /><ul>{desktop.previews.map((preview) => <li key={preview.url}><a href={preview.url} target="_blank" rel="noreferrer">{preview.label}</a></li>)}</ul></section>;
}
export function WorkspaceControls({ runId }: { runId: string }) {
  const workspace = useQuery({ queryKey: ["workspace", runId], queryFn: () => getJson("/runs/" + runId + "/workspace", workspaceSchema) });
  if (workspace.isPending) return <p>Preparing guest workspace…</p>; if (workspace.isError) return <p role="alert">Workspace is not ready yet.</p>;
  return <><CopyPolicy workspace={workspace.data} /><Desktop runId={runId} /><Checkpoints runId={runId} /></>;
}
