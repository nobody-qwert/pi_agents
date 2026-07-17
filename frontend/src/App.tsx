import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getJson, graphSchema, type GraphNode } from "./api";
import { ChatWorkspace } from "./ChatWorkspace";
import { GraphInspector, GraphView } from "./GraphView";
import { SafeMarkdown } from "./Markdown";

const views = ["workspace", "graph", "runs", "settings"] as const;
function Graph({ onInspect }: { onInspect: (stage: GraphNode) => void }) { const query = useQuery({ queryKey: ["control-graph"], queryFn: () => getJson("/system/graph", graphSchema) }); if (query.isPending) return <p>Loading graph…</p>; if (query.isError) return <p role="alert">Could not load the control graph.</p>; return <GraphView controlGraph={query.data} onInspect={({ stage }) => onInspect(stage)} />; }
function View({ name, onInspect }: { name: string; onInspect: (stage: GraphNode) => void }) { if (name === "workspace") return <ChatWorkspace />; if (name === "graph") return <section><h1>graph</h1><Graph onInspect={onInspect} /></section>; return <section><h1>{name}</h1><SafeMarkdown source="This view is ready for its next feature packet." /></section>; }
export function App() { const [selection, setSelection] = useState<{ stage: GraphNode }>(); return <div className="shell"><nav aria-label="Primary">{views.map((view) => <NavLink key={view} to={`/${view}`}>{view}</NavLink>)}</nav><main><Routes>{views.map((view) => <Route key={view} path={`/${view}`} element={<View name={view} onInspect={(stage) => setSelection({ stage })} />} />)}<Route path="*" element={<Navigate to="/workspace" replace />} /></Routes></main><aside aria-label="Inspector"><GraphInspector selection={selection} /></aside></div>; }
