import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";
import type { GraphNode, GraphProjection } from "./api";

export type StageStatus = "active" | "completed" | "failed" | "approval" | "blocked" | "pending";
export type RunOverlay = Readonly<Record<string, { status: StageStatus; duration?: string }>>;
export type StageNodeData = { stage: GraphNode; overlay?: RunOverlay[string]; onInspect?: (stage: GraphNode) => void };
export type StageFlowNode = Node<StageNodeData, "stage">;

const elk = new ELK();
const nodeWidth = 224;
const nodeHeight = 104;

export function graphElements(graph: GraphProjection, overlay: RunOverlay = {}): { nodes: StageFlowNode[]; edges: Edge[] } {
  const nodes: StageFlowNode[] = graph.nodes.map((stage) => ({ id: stage.node_id, type: "stage", position: { x: 0, y: 0 }, data: { stage, overlay: overlay[stage.node_id] } }));
  const edges = graph.edges.map((edge) => ({ id: `${edge.source}:${edge.target}`, source: edge.source, target: edge.target, label: edge.condition ?? undefined, animated: overlay[edge.source]?.status === "active", className: overlay[edge.source]?.status === "completed" ? "traversed-edge" : undefined }));
  return { nodes, edges };
}

export async function layoutGraph(graph: GraphProjection, overlay: RunOverlay = {}): Promise<{ nodes: StageFlowNode[]; edges: Edge[] }> {
  const elements = graphElements(graph, overlay);
  const layout = await elk.layout({ id: "control-graph", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES", "elk.spacing.nodeNode": "56", "elk.layered.spacing.nodeNodeBetweenLayers": "96" }, children: elements.nodes.map((node) => ({ id: node.id, width: nodeWidth, height: nodeHeight })), edges: elements.edges.map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) });
  const positions = new Map(layout.children?.map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]));
  return { nodes: elements.nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position })), edges: elements.edges };
}
