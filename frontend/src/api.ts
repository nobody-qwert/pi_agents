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
