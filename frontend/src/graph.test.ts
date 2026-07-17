import { describe, expect, it } from "vitest";
import { graphSchema } from "./api";
import { graphElements, layoutGraph } from "./graph";

const graph = graphSchema.parse({ entry_node: "INTAKE", registry_hash: "a".repeat(64), nodes: [{ node_id: "INTAKE", description: "Starts work", agent_id: "intake", agent: { agent_id: "intake", display_name: "Intake", description: "Collects request", prompt_title: "Intake", prompt_excerpt: "First lines", prompt: "Safe prompt", provider: "lm-studio", model: "qwen", temperature: 0.1, max_output_tokens: 100, timeout_seconds: 20, max_attempts: 1, allow_parallel: false, tools: [], input_schema: "Input", output_schema: "Output", input_schema_json: { type: "object" }, output_schema_json: { type: "object" }, authority_badges: ["read"], config_hash: "b".repeat(64), prompt_hash: "c".repeat(64) } }, { node_id: "COMPLETE", description: "Ends work", agent_id: null, agent: null }], edges: [{ source: "INTAKE", target: "COMPLETE", condition: "accepted" }] });

describe("graph projection", () => {
  it("keeps the backend topology exact while applying only overlay state", () => { const elements = graphElements(graph, { INTAKE: { status: "active" } }); expect(elements.nodes.map((node) => node.id)).toEqual(["INTAKE", "COMPLETE"]); expect(elements.edges).toMatchObject([{ source: "INTAKE", target: "COMPLETE", label: "accepted" }]); expect(elements.nodes[0]?.data.overlay?.status).toBe("active"); });
  it("uses stable ELK positions for the same registry projection", async () => { const first = await layoutGraph(graph); const second = await layoutGraph(graph); expect(first.nodes.map((node) => node.position)).toEqual(second.nodes.map((node) => node.position)); });
});
