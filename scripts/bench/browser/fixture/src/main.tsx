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

const SUCCESS_STATUSES = new Set(["Success", "CompletedWithErrors"]);
const FAILURE_STATUSES = new Set(["Failed", "Timeout", "Cancelled"]);

export function BenchApp() {
  const variant = new URLSearchParams(location.search).get("variant") ?? "control";
  const workflow = useWorkflow<Record<string, unknown>>(refs[variant]);
  const invocationRequestId = useRef<string | null>(null);
  const renderedRequestId = useRef<string | null>(null);
  useEffect(() => {
    const requestId = invocationRequestId.current;
    const { executionId, status } = workflow;
    if (!requestId || !executionId || !status || renderedRequestId.current === requestId) return;
    const successVisible = SUCCESS_STATUSES.has(status)
      && workflow.data?.request_id === requestId;
    const failureVisible = FAILURE_STATUSES.has(status) && workflow.error !== null;
    if (!successVisible && !failureVisible) return;
    const frame = requestAnimationFrame(() => {
      if (renderedRequestId.current === requestId) return;
      renderedRequestId.current = requestId;
      mark({ type: "render", execution_id: executionId, status });
    });
    return () => cancelAnimationFrame(frame);
  }, [workflow.status, workflow.executionId, workflow.data, workflow.error]);
  return <>
    <button aria-label="Run Browser Benchmark" onClick={() => {
      const requestId = crypto.randomUUID();
      invocationRequestId.current = requestId;
      mark({ type: "invoke", request_id: requestId });
      void workflow.run({ bench_request_id: requestId });
    }}>Run Browser Benchmark</button>
    <output role="status" aria-live="polite">{workflow.status ?? "Idle"}: {workflow.data ? JSON.stringify(workflow.data) : workflow.error?.message ?? ""}</output>
  </>;
}

export function mount(element: HTMLElement, bootstrap: Record<string, unknown>) {
  const root = createRoot(element);
  root.render(<BifrostProvider {...bootstrap} baseUrl={String(bootstrap.baseUrl)} token={String(bootstrap.token)}><BenchApp /></BifrostProvider>);
  return () => root.unmount();
}

(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });
