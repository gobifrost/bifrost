import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BifrostProvider, useWorkflow } from "bifrost";

type Event = { type: string; t_ms: number; execution_id?: string; status?: string };
declare global { interface Window { __BENCH_BROWSER_EVENTS__?: Event[]; __BIFROST_APP_MODULES__?: Map<string, unknown>; } }
const mark = (event: Omit<Event, "t_ms">) => (window.__BENCH_BROWSER_EVENTS__ ??= []).push({ ...event, t_ms: performance.now() });
const refs: Record<string, string> = {
  control: "bench_wf_interactive_control", same_body_graph: "bench_wf_interactive_same_body_graph",
  shallow_list: "bench_wf_interactive_shallow_list", detail_hydration: "bench_wf_interactive_detail_hydration",
};

function BenchApp() {
  const variant = new URLSearchParams(location.search).get("variant") ?? "control";
  const workflow = useWorkflow<Record<string, unknown>>(refs[variant]);
  useEffect(() => { if (workflow.status === "Success" && workflow.executionId) mark({ type: "render", execution_id: workflow.executionId, status: workflow.status }); }, [workflow.status, workflow.executionId, workflow.data]);
  return <button aria-label="Run Browser Benchmark" onClick={() => { mark({ type: "invoke" }); void workflow.run({}); }}>Run Browser Benchmark</button>;
}

export function mount(element: HTMLElement, bootstrap: Record<string, unknown>) {
  const root = createRoot(element);
  root.render(<BifrostProvider {...bootstrap} baseUrl={String(bootstrap.baseUrl)} token={String(bootstrap.token)}><BenchApp /></BifrostProvider>);
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
