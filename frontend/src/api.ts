import { z } from "zod";

export class ApiError extends Error { constructor(readonly code: string) { super(code); } }
const errorSchema = z.object({ code: z.string(), request_id: z.string() });
export async function getJson<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { headers: { "X-Dev-User": "user_local" } });
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
