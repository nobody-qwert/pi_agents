import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getJson, projectsSchema } from "./api";
import { SafeMarkdown } from "./Markdown";

const views = ["workspace", "graph", "runs", "settings"] as const;
function Workspace() { const query = useQuery({ queryKey: ["projects"], queryFn: () => getJson("/projects", projectsSchema) }); if (query.isPending) return <p>Loading projects…</p>; if (query.isError) return <p role="alert">Could not load projects.</p>; return query.data.projects.length ? <ul>{query.data.projects.map((project) => <li key={project.project_id}>{project.display_name}</li>)}</ul> : <p>No allowlisted projects are available.</p>; }
function View({ name }: { name: string }) { return <section><h1>{name}</h1>{name === "workspace" ? <Workspace /> : <SafeMarkdown source="This view is ready for its next feature packet." />}</section>; }
export function App() { return <div className="shell"><nav aria-label="Primary">{views.map((view) => <NavLink key={view} to={`/${view}`}>{view}</NavLink>)}</nav><main><Routes>{views.map((view) => <Route key={view} path={`/${view}`} element={<View name={view} />} />)}<Route path="*" element={<Navigate to="/workspace" replace />} /></Routes></main><aside aria-label="Inspector"><h2>Inspector</h2><p>Select an item to inspect safe details.</p></aside></div>; }
