/**
 * `useWorkflow` — the v2 SDK's workflow-execution hook.
 *
 * A standalone_v2 app calls `import { useWorkflow } from "bifrost"` and runs a
 * workflow through the authed transport its `<BifrostProvider>` established —
 * NOT the v1 `globalThis.__bifrost_platform` path (which reaches into platform
 * stores that a standalone app doesn't have). This mirrors how `useTable` reads
 * the provider context: auth/baseUrl/org come from `useBifrostContext()`, so the
 * same code runs in `npm run dev` (cross-origin, bearer token) and deployed.
 *
 * Two shapes, matching the v1 surface:
 *   - `useWorkflow(workflowRef)` → a query-style result you trigger with `run()`.
 *   - `run(input)` POSTs `/api/workflows/execute` (no `sync` flag — the server
 *     always starts the run asynchronously and returns `{ execution_id, status }`
 *     immediately).
 *
 * Streaming transport: after starting the run, `run()` subscribes to the
 * execution over the SDK websocket (`subscribeToExecution`) for live `status`
 * and `log` events. Terminal websocket frames do NOT carry the result (see
 * execution-stream.ts) — on `isTerminal`, `run()` unsubscribes and fetches
 * `GET /api/executions/{id}` once to get the actual `result`/`error_message`.
 * A fast run can finish before the socket even opens, so `run()` also does one
 * immediate `GET` right after subscribing. If the socket goes down
 * (`onSocketDown`), `run()` degrades to polling that same GET every 2s until
 * terminal — no client-side deadline; the server enforces the workflow's own
 * timeout and will eventually flip the execution terminal.
 *
 * Two exceptions bypass the stream entirely and settle inline from the POST
 * response: `is_transient` runs (data-provider-style executions) and runs
 * that are already terminal by the time the POST responds.
 */
import { useCallback, useRef, useState } from "react";

import type { components } from "@/lib/v1";

import { subscribeToExecution, type ExecutionStreamEvent } from "./execution-stream";
import { useBifrostContext } from "./provider";

export interface WorkflowLogEntry {
  level: string;
  message: string;
  timestamp: string;
  sequence?: number;
}

export interface UseWorkflowState<T> {
  /** Last successful result, or null before the first run. */
  data: T | null;
  /** True while a run is in flight. */
  loading: boolean;
  /** Last error, or null. */
  error: Error | null;
  /** Execute the workflow with `input_data`; resolves to the result. */
  run: (input?: Record<string, unknown>) => Promise<T>;
  /** Live log stream for the latest run. Reset to `[]` at the start of each run. */
  logs: WorkflowLogEntry[];
  /** Latest run's status (e.g. "Pending", "Running", "Success"), or null before the first run. */
  status: string | null;
  /** Latest run's execution id, or null before the first run. */
  executionId: string | null;
}

// The generated contract type: `status` is the PascalCase `ExecutionStatus`
// literal union ("Success" | "Failed" | ...), so the compiler enforces the
// exact wire casing in the failed-status check below.
type ExecuteResponse = components["schemas"]["WorkflowExecutionResponse"];

const TERMINAL_STATUSES = new Set([
  "Success",
  "Failed",
  "CompletedWithErrors",
  "Timeout",
  "Cancelled",
]);

const POLL_INTERVAL_MS = 2000;

/**
 * Run a Bifrost workflow by UUID, portable `path::function` ref, or workflow
 * name from a v2 app. All three resolve identically: the server scopes the
 * ref to THIS install (via the X-Bifrost-App transport), so a name or path
 * ref reaches the install's own workflow first. Prefer `path::function` —
 * it's the ref shape that also runs locally under `bifrost solution start`
 * (name/UUID refs proxy to the deployed copy there). Must be called within a
 * `<BifrostProvider>` (throws otherwise — same contract as `useBifrostContext`).
 */
export function useWorkflow<T = unknown>(workflowRef: string): UseWorkflowState<T> {
  const { authedFetch, appId } = useBifrostContext();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [logs, setLogs] = useState<WorkflowLogEntry[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  // Monotonic run counter: overlapping runs each capture their own seq, and
  // only the LATEST run (seq === seqRef.current) may write hook state. A slow
  // stale run can't overwrite a newer run's data or flip `loading` while the
  // newer run is still in flight. Each caller's promise still settles with its
  // own result/rejection.
  const seqRef = useRef(0);

  const run = useCallback(
    async (input: Record<string, unknown> = {}): Promise<T> => {
      const seq = ++seqRef.current;
      setLoading(true);
      setError(null);
      setLogs([]);
      setStatus("Pending");

      const settleFromExecution = (exec: {
        status?: string;
        result?: unknown;
        error_message?: string | null;
      }): T => {
        if (exec.status === "Success" || exec.status === "CompletedWithErrors") {
          return exec.result as T;
        }
        throw new Error(exec.error_message ?? `Workflow ${exec.status ?? "failed"}`);
      };
      const fetchExecution = async (id: string) => {
        const r = await authedFetch(`/api/executions/${id}`);
        if (!r.ok) throw new Error(`failed to fetch execution: ${r.status}`);
        return (await r.json()) as {
          status?: string;
          result?: unknown;
          error_message?: string | null;
        };
      };

      try {
        const resp = await authedFetch("/api/workflows/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: workflowRef,
            input_data: input,
            // Scope a path::function ref to THIS install's own workflow (so it
            // can't resolve a sibling install's workflow sharing the path).
            ...(appId ? { app_id: appId } : {}),
          }),
        });
        if (!resp.ok) {
          throw new Error(`workflow execution failed: ${resp.status} ${resp.statusText}`);
        }
        const body = (await resp.json()) as ExecuteResponse & { is_transient?: boolean };
        const execId = body.execution_id;
        if (seq === seqRef.current && execId) setExecutionId(execId);

        // Transient (data-provider-style) and already-terminal responses
        // settle inline — never touch the websocket.
        if (body.is_transient || TERMINAL_STATUSES.has(body.status)) {
          if (body.error || body.status === "Failed") {
            throw new Error(body.error ?? `Workflow failed (status: ${body.status})`);
          }
          const result = body.result as T;
          if (seq === seqRef.current) {
            setData(result);
            setStatus(body.status);
          }
          return result;
        }

        const result = await new Promise<T>((resolve, reject) => {
          let settled = false;
          let pollTimer: ReturnType<typeof setInterval> | null = null;
          let unsubscribe: () => void = () => {};
          // Set once a terminal ws frame has been seen. There's no client
          // deadline, so if the one post-terminal fetch fails transiently we
          // must not just give up — arm the poll fallback so the run still
          // settles instead of hanging forever.
          let sawTerminal = false;
          const settle = (fn: () => void) => {
            if (settled) return;
            settled = true;
            if (pollTimer) clearInterval(pollTimer);
            unsubscribe();
            fn();
          };
          const checkOnce = async () => {
            try {
              const exec = await fetchExecution(execId);
              if (seq === seqRef.current && exec.status) setStatus(exec.status);
              if (exec.status && TERMINAL_STATUSES.has(exec.status)) {
                settle(() => {
                  try {
                    resolve(settleFromExecution(exec));
                  } catch (e) {
                    reject(e);
                  }
                });
              }
            } catch {
              // Transient fetch failure — the stream/poll keeps driving; a
              // persistent failure surfaces on the next terminal fetch attempt.
              // If this was the post-terminal-event fetch, there's no further
              // status frame coming and the socket may stay open (so
              // onSocketDown never fires) — arm the poll fallback so the run
              // still settles instead of hanging forever.
              if (sawTerminal && !settled && !pollTimer) {
                pollTimer = setInterval(checkOnce, POLL_INTERVAL_MS);
              }
            }
          };
          unsubscribe = subscribeToExecution(
            execId,
            (evt: ExecutionStreamEvent) => {
              if (evt.type === "log" && evt.log && seq === seqRef.current) {
                const log = evt.log;
                setLogs((prev) => [...prev, log]);
              }
              if (evt.type === "status") {
                if (seq === seqRef.current && evt.status) setStatus(evt.status);
                if (evt.isTerminal) {
                  sawTerminal = true;
                  void checkOnce();
                }
              }
            },
            () => {
              // Socket died — degrade to polling. No deadline: the server
              // enforces the workflow's own timeout and will flip it terminal.
              if (!settled && !pollTimer) pollTimer = setInterval(checkOnce, POLL_INTERVAL_MS);
            },
          );
          void checkOnce(); // fast runs can finish before the socket opens
        });

        if (seq === seqRef.current) setData(result);
        return result;
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        if (seq === seqRef.current) setError(err);
        throw err;
      } finally {
        if (seq === seqRef.current) setLoading(false);
      }
    },
    [authedFetch, workflowRef, appId],
  );

  return { data, loading, error, run, logs, status, executionId };
}
