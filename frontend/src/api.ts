import { z } from "zod";

export class ApiError extends Error { constructor(readonly code: string) { super(code); } }
const errorSchema = z.object({ code: z.string(), request_id: z.string() });
export async function getJson<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(apiPath(path), { headers: { "X-Dev-User": "user_local" } });
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
export const projectSchema = z.object({ project_id: z.string(), display_name: z.string() });
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

export const runSchema = z.object({ run_id: z.string(), project_id: z.string(), status: z.string() });
export const runsSchema = z.array(runSchema);
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
