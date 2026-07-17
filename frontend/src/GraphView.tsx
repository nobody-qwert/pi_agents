import { useEffect, useMemo, useState } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type NodeProps, type NodeTypes, useEdgesState, useNodesState } from "@xyflow/react";
import type { Agent, GraphNode, GraphProjection } from "./api";
import { layoutGraph, type RunOverlay, type StageFlowNode } from "./graph";

type InspectorSelection = { stage: GraphNode };
export type GraphViewProps = { controlGraph: GraphProjection; workGraph?: GraphProjection; overlay?: RunOverlay; onInspect: (selection: InspectorSelection) => void; onStageFilter?: (stageId: string) => void };

function StageCard({ data }: NodeProps<StageFlowNode>) {
  const status = data.overlay?.status ?? "pending";
  return <div className={"stage-card stage-" + status}><Handle type="target" position={Position.Left} /><button type="button" aria-label={"Inspect " + data.stage.node_id} onClick={() => data.onInspect?.(data.stage)} onFocus={() => data.onInspect?.(data.stage)}><strong>{data.stage.node_id}</strong><span>{data.stage.agent?.display_name ?? "Control gate"}</span><small>{data.overlay?.duration ?? status}</small></button><Handle type="source" position={Position.Right} /></div>;
}
const nodeTypes = { stage: StageCard } as unknown as NodeTypes;

export function GraphView({ controlGraph, workGraph, overlay, onInspect, onStageFilter }: GraphViewProps) {
  const [mode, setMode] = useState<"control" | "work">("control");
  const graph = mode === "work" && workGraph ? workGraph : controlGraph;
  const [nodes, setNodes, onNodesChange] = useNodesState<StageFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const graphKey = useMemo(() => JSON.stringify({ graph, overlay }), [graph, overlay]);

  useEffect(() => { let active = true; void layoutGraph(graph, overlay).then((next) => { if (active) { setNodes(next.nodes.map((node) => ({ ...node, data: { ...node.data, onInspect: (stage) => { onInspect({ stage }); onStageFilter?.(stage.node_id); } } }))); setEdges(next.edges); } }); return () => { active = false; }; }, [graph, graphKey, onInspect, onStageFilter, overlay, setEdges, setNodes]);

  return <section className="graph-view" aria-label="Workflow graph"><div className="graph-toolbar" role="group" aria-label="Graph topology"><button type="button" aria-pressed={mode === "control"} onClick={() => setMode("control")}>Control graph</button><button type="button" aria-pressed={mode === "work"} disabled={!workGraph} title={workGraph ? undefined : "No approved work graph is available for this run."} onClick={() => setMode("work")}>Work graph</button></div><div className="graph-canvas"><ReactFlow<StageFlowNode, Edge> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} nodesConnectable={false} nodesDraggable={false} elementsSelectable onNodeClick={(_, node) => { const data = node.data; onInspect({ stage: data.stage }); onStageFilter?.(node.id); }} fitView><Background /><Controls showInteractive={false} /></ReactFlow></div></section>;
}

function Schema({ value }: { value: Record<string, unknown> }) { return <pre>{JSON.stringify(value, null, 2)}</pre>; }
function AgentDetails({ agent }: { agent: Agent }) { return <><p>{agent.description}</p><dl><dt>Prompt</dt><dd>{agent.prompt ?? agent.prompt_excerpt ?? "Not exposed"}</dd><dt>Model</dt><dd>{agent.provider} / {agent.model}</dd><dt>Generation</dt><dd>Temperature {agent.temperature}; {agent.max_output_tokens} tokens</dd><dt>Tools</dt><dd>{agent.tools.join(", ") || "None"}</dd><dt>Policy</dt><dd>{agent.timeout_seconds}s timeout; {agent.max_attempts} attempts</dd><dt>Schemas</dt><dd>{agent.input_schema} → {agent.output_schema}</dd><dt>Authority</dt><dd>{agent.authority_badges.join(", ") || "None"}</dd><dt>Configuration hash</dt><dd><code>{agent.config_hash}</code></dd></dl><h3>Input schema</h3><Schema value={agent.input_schema_json} /><h3>Output schema</h3><Schema value={agent.output_schema_json} /></>; }
export function GraphInspector({ selection }: { selection?: InspectorSelection }) { if (!selection) return <><h2>Inspector</h2><p>Select or focus a graph stage to inspect its safe configuration.</p></>; const { stage } = selection; return <><h2>{stage.node_id}</h2>{stage.agent ? <AgentDetails agent={stage.agent} /> : <p>{stage.description}</p>}</>; }
