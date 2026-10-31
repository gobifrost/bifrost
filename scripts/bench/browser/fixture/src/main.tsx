import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { BifrostProvider, useWorkflow } from "bifrost";

type Event = { type: string; t_ms: number; execution_id?: string; request_id?: string; status?: string };
declare global { interface Window { __BENCH_BROWSER_EVENTS__?: Event[]; __BIFROST_APP_MODULES__?: Map<string, unknown>; } }
const mark = (event: Omit<Event, "t_ms">) => (window.__BENCH_BROWSER_EVENTS__ ??= []).push({ ...event, t_ms: performance.now() });
const refs: Record<string, string> = {
  control: "bench_wf_interactive_control", same_body_graph: "bench_wf_interactive_same_body_graph",
  shallow_list: "bench_wf_interactive_shallow_list", detail_hydration: "bench_wf_interactive_detail_hydration",
};

function BenchApp() {
  const variant = new URLSearchParams(location.search).get("variant") ?? "control";
  const workflow = useWorkflow<Record<string, unknown>>(refs[variant]);
  const renderedExecution = useRef<string | null>(null);
  useEffect(() => {
    if (!workflow.executionId || !workflow.status || renderedExecution.current === workflow.executionId) return;
    if (!["Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled"].includes(workflow.status)) return;
    renderedExecution.current = workflow.executionId;
    const frame = requestAnimationFrame(() => mark({ type: "render", execution_id: workflow.executionId!, status: workflow.status! }));
    return () => cancelAnimationFrame(frame);
  }, [workflow.status, workflow.executionId, workflow.data, workflow.error]);
  return <>
    <button aria-label="Run Browser Benchmark" onClick={() => {
      const requestId = crypto.randomUUID();
      mark({ type: "invoke", request_id: requestId });
      void workflow.run({ bench_request_id: requestId });
    }}>Run Browser Benchmark</button>
    <output aria-live="polite">{workflow.status ?? "Idle"}: {workflow.data ? JSON.stringify(workflow.data) : workflow.error?.message ?? ""}</output>
  </>;
}

export function mount(element: HTMLElement, bootstrap: Record<string, unknown>) {
  const root = createRoot(element);
  root.render(<BifrostProvider {...bootstrap} baseUrl={String(bootstrap.baseUrl)} token={String(bootstrap.token)}><BenchApp /></BifrostProvider>);
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
