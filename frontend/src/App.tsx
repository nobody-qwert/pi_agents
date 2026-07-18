import { lazy, Suspense, useCallback, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import type { GraphNode } from "./api";
import { GraphInspector } from "./GraphInspector";

const ChatWorkspace = lazy(() => import("./ChatWorkspace").then((module) => ({ default: module.ChatWorkspace })));
const GraphPage = lazy(() => import("./GraphPage"));
const RunsView = lazy(() => import("./RunsView").then((module) => ({ default: module.RunsView })));
const SettingsView = lazy(() => import("./SettingsView").then((module) => ({ default: module.SettingsView })));
const views = [{ path: "/workspace", label: "Workspace", glyph: "W" }, { path: "/graph", label: "Graph", glyph: "G" }, { path: "/runs", label: "Runs", glyph: "R" }, { path: "/settings", label: "System", glyph: "S" }] as const;

export function App() {
  const [selection, setSelection] = useState<{ stage: GraphNode }>();
  const inspect = useCallback((stage: GraphNode) => setSelection({ stage }), []);
  return <div className="shell"><nav aria-label="Primary" className="primary-nav"><div className="brand"><span className="brand-mark">π</span><div><strong>Nested Loop</strong><small>Orchestrator</small></div></div><div className="nav-links">{views.map((view) => <NavLink key={view.path} to={view.path}><span aria-hidden="true">{view.glyph}</span>{view.label}</NavLink>)}</div><p className="nav-foot">Deterministic control plane</p></nav><main><Suspense fallback={<div className="empty-state">Loading view…</div>}><Routes><Route path="/workspace" element={<ChatWorkspace />} /><Route path="/graph" element={<GraphPage onInspect={inspect} />} /><Route path="/runs" element={<RunsView />} /><Route path="/settings" element={<SettingsView />} /><Route path="*" element={<Navigate to="/workspace" replace />} /></Routes></Suspense></main><aside aria-label="Inspector" className="inspector"><GraphInspector selection={selection} /></aside></div>;
}
