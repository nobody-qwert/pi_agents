import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { getJson, graphSchema, runsSchema, workGraphSchema, type GraphNode } from "./api";
import { GraphView } from "./GraphView";
import { workGraphProjection } from "./graph";

export default function GraphPage({ onInspect }: { onInspect: (stage: GraphNode) => void }) {
  const query = useQuery({ queryKey: ["control-graph"], queryFn: () => getJson("/system/graph", graphSchema) });
  const runs = useQuery({ queryKey: ["runs", "graph"], queryFn: () => getJson("/runs", runsSchema) });
  const [selected, setSelected] = useState("");
  const runId = selected || runs.data?.[0]?.run_id || "";
  const work = useQuery({ queryKey: ["work-graph", runId], enabled: Boolean(runId), queryFn: () => getJson(`/runs/${runId}/work-graph`, workGraphSchema) });
  const projected = useMemo(() => work.data && runId ? workGraphProjection(runId, work.data) : undefined, [runId, work.data]);
  return <section className="page-stack graph-page"><header className="page-header"><div><p className="eyebrow">Code-owned topology</p><h1>Control and work graphs</h1><p>Inspect fixed routing and the currently accepted run-specific decomposition.</p></div><label>Run<select value={runId} onChange={(event) => setSelected(event.target.value)}><option value="">No run selected</option>{runs.data?.map((run) => <option key={run.run_id} value={run.run_id}>{run.message}</option>)}</select></label></header>{query.isPending ? <div className="empty-state">Loading graph…</div> : query.isError ? <div className="error-state" role="alert">Could not load the control graph.</div> : <GraphView controlGraph={query.data} workGraph={projected?.graph} overlay={projected?.overlay} onInspect={({ stage }) => onInspect(stage)} />}</section>;
}
