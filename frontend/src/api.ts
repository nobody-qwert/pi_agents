import { z } from "zod";

export class ApiError extends Error { constructor(readonly code: string) { super(code); } }
const errorSchema = z.object({ code: z.string(), request_id: z.string() });
export async function getJson<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(apiPath(path), { headers: { "X-Dev-User": "user_local" } });
  const data: unknown = await response.json();
  if (!response.ok) { const parsed = errorSchema.safeParse(data); throw new ApiError(parsed.success ? parsed.data.code : "api_request_failed"); }
  return schema.parse(data);
}
export async function getServiceJson<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(path, { headers: { "X-Dev-User": "user_local" } });
  const data: unknown = await response.json();
  if (!response.ok) { const parsed = errorSchema.safeParse(data); throw new ApiError(parsed.success ? parsed.data.code : "api_request_failed"); }
  return schema.parse(data);
}
function apiPath(path: string) { return path.startsWith("/api/") ? path : "/api/v1" + path; }
export async function postJson<T>(path: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(apiPath(path), { method: "POST", headers: { "Content-Type": "application/json", "X-Dev-User": "user_local", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(body) });
  const data: unknown = await response.json();
  if (!response.ok) { const parsed = errorSchema.safeParse(data); throw new ApiError(parsed.success ? parsed.data.code : "api_request_failed"); }
  return schema.parse(data);
}
export async function downloadArtifact(artifactId: string): Promise<void> {
  const response = await fetch(apiPath("/artifacts/" + artifactId + "?download=true"), { headers: { "X-Dev-User": "user_local" } });
  if (!response.ok) throw new ApiError("artifact_download_failed");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = artifactId + ".patch"; link.click();
  URL.revokeObjectURL(url);
}
export const projectSchema = z.object({ project_id: z.string(), display_name: z.string(), source_fingerprint: z.string(), file_count: z.number(), included_bytes: z.number(), excluded_paths: z.array(z.string()), protected_paths: z.array(z.string()), git_head: z.string().nullable(), git_dirty: z.boolean().nullable() });
export const projectsSchema = z.object({ projects: z.array(projectSchema) });

export const agentSchema = z.object({
  agent_id: z.string(), display_name: z.string(), description: z.string(),
  prompt_title: z.string(), prompt_excerpt: z.string().nullable(), prompt: z.string().nullable(),
  provider: z.string(), model: z.string(), temperature: z.number(), max_output_tokens: z.number(),
  timeout_seconds: z.number(), max_attempts: z.number(), allow_parallel: z.boolean(),
  tools: z.array(z.string()), input_schema: z.string(), output_schema: z.string(),
  input_schema_json: z.record(z.string(), z.unknown()), output_schema_json: z.record(z.string(), z.unknown()),
  authority_badges: z.array(z.string()), config_hash: z.string(), prompt_hash: z.string(),
});
export const graphNodeSchema = z.object({ node_id: z.string(), description: z.string(), agent_id: z.string().nullable(), agent: agentSchema.nullable() });
export const graphEdgeSchema = z.object({ source: z.string(), target: z.string(), condition: z.string().nullable() });
export const graphSchema = z.object({ entry_node: z.string(), registry_hash: z.string(), nodes: z.array(graphNodeSchema), edges: z.array(graphEdgeSchema) });

export type Agent = z.infer<typeof agentSchema>;
export type GraphNode = z.infer<typeof graphNodeSchema>;
export type GraphProjection = z.infer<typeof graphSchema>;

export const runSchema = z.object({
  run_id: z.string(), project_id: z.string(), status: z.string(),
  conversation_id: z.string().nullable(), message: z.string(), current_gate: z.string(),
  source_fingerprint: z.string(), created_at: z.string().nullable(), updated_at: z.string().nullable(),
});
export const runsSchema = z.array(runSchema);
export const workNodeSchema = z.object({ work_node_id: z.string(), parent_id: z.string().nullable(), goal: z.string(), owner_role: z.string(), status: z.string(), depends_on: z.array(z.string()) });
export const workGraphSchema = z.object({ nodes: z.array(workNodeSchema), edges: z.array(z.object({ edge_id: z.string(), from_work_node_id: z.string(), to_work_node_id: z.string(), edge_type: z.string() })) });
export const readinessSchema = z.object({ status: z.literal("ready"), model_id: z.string() });
export const registrySchema = z.object({ registry_hash: z.string(), agents: z.array(agentSchema) });
export const runEventSchema = z.object({
  event_id: z.string(), sequence: z.number().int().positive(), type: z.string(),
  run_id: z.string().optional(), conversation_id: z.string().nullable().optional(),
  occurred_at: z.string().optional(), stage: z.string().nullable().optional(),
  node_id: z.string().nullable().optional(), work_node_id: z.string().nullable().optional(),
  attempt_id: z.string().nullable().optional(), status: z.string().optional(),
  summary: z.string().optional(), detail_ref: z.string().optional(), trace_id: z.string().optional(),
});
export const eventDetailSchema = z.object({
  category: z.enum(["agent", "tool", "validation", "transition", "approval", "artifact", "workspace", "promotion", "error"]),
  summary: z.string(), fields: z.array(z.object({ label: z.string(), value: z.union([z.string(), z.number(), z.boolean(), z.null()]) })),
});
export type Run = z.infer<typeof runSchema>;
export type RunEvent = z.infer<typeof runEventSchema>;
export type EventDetail = z.infer<typeof eventDetailSchema>;
export type WorkGraph = z.infer<typeof workGraphSchema>;
export const workspaceSchema = z.object({ workspace_id: z.string(), run_id: z.string(), project_id: z.string(), source_fingerprint: z.string(), excluded_paths: z.array(z.string()), protected_paths: z.array(z.string()), guest_path: z.string().nullable().optional(), status: z.string(), health: z.object({ vm: z.string(), ssh: z.string(), browser: z.string(), egress: z.string() }) });
export const checkpointSchema = z.object({ checkpoint_id: z.string(), kind: z.string(), commit_hash: z.string(), tree_hash: z.string(), parent_checkpoint_id: z.string().nullable(), rollback_from_checkpoint_id: z.string().nullable().optional(), design_version: z.number().int().positive(), work_node_id: z.string().nullable(), evidence_ids: z.array(z.string()) });
export const checkpointsSchema = z.object({ current_checkpoint_id: z.string().nullable(), checkpoints: z.array(checkpointSchema) });
export const rollbackPreviewSchema = z.object({ current_checkpoint_id: z.string(), target_checkpoint_id: z.string(), changed_paths: z.array(z.object({ status: z.string(), path: z.string() })) });
export const desktopSchema = z.object({ session_id: z.string(), expires_at: z.string(), websocket_url: z.string(), input_owner: z.enum(["AGENT", "USER", "PAUSED"]), previews: z.array(z.object({ label: z.string(), url: z.string() })) });
export const desktopStateSchema = z.object({ run_id: z.string(), input_owner: z.enum(["AGENT", "USER", "PAUSED"]) });
export const previewsSchema = z.object({ previews: z.array(z.object({ label: z.string(), port: z.number().int().positive(), url: z.string(), expires_at: z.number().int().positive() })) });
export type Workspace = z.infer<typeof workspaceSchema>;
export type Checkpoint = z.infer<typeof checkpointSchema>;
export const approvalSchema = z.object({ approval_id: z.string(), authority: z.string(), affected_versions: z.array(z.string()), expires_at: z.string().nullable(), status: z.enum(["pending", "approved", "rejected"]), comment: z.string().nullable().optional() });
export const approvalsSchema = z.object({ approvals: z.array(approvalSchema) });
export const artifactContentSchema = z.object({ artifact_id: z.string(), version: z.number().int().positive(), media_type: z.string(), sha256: z.string(), size_bytes: z.number().int().nonnegative(), preview: z.string().nullable(), preview_truncated: z.boolean() });
export const promotionPreviewSchema = z.object({ preview_hash: z.string(), artifact_id: z.string(), artifact_version: z.number().int().positive(), patch_sha256: z.string(), direct_eligible: z.boolean(), checkpoint_commit: z.string(), changed_files: z.array(z.string()), checks: z.array(z.object({ name: z.string(), status: z.enum(["passed", "failed", "pending"]) })), issues: z.array(z.string()), baseline: z.string(), recorded_baseline: z.string(), current_baseline: z.string(), current_source_dirty: z.boolean().nullable(), protected_paths: z.array(z.string()), target_branch: z.string(), proposed_version: z.string(), conflict_reason: z.string().nullable(), confirmation_nonce: z.string(), confirmation_expires_at: z.string() });
export const promotionResultSchema = z.object({ status: z.enum(["committed", "fallback", "rejected"]), branch: z.string().nullable(), commit_hash: z.string().nullable(), tag: z.string().nullable(), reason: z.string().nullable(), review_repository_id: z.string().nullable(), review_commit: z.string().nullable() });
export type Approval = z.infer<typeof approvalSchema>;
export type PromotionPreview = z.infer<typeof promotionPreviewSchema>;
export const conversationMessageSchema = z.object({ message_id: z.string(), sequence: z.number().int().positive(), role: z.string(), content: z.string(), created_at: z.string() });
export const conversationSchema = z.object({ conversation_id: z.string(), created_at: z.string().optional(), messages: z.array(conversationMessageSchema), run_ids: z.array(z.string()).optional() });
export const conversationMessageResultSchema = z.object({ message: conversationMessageSchema, run_id: z.string().nullable() });
export type Conversation = z.infer<typeof conversationSchema>;
