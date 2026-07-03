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
 *   - `run(input)` POSTs `/api/workflows/execute` with `sync: true` and returns
 *     the workflow `result`.
 */
import { useCallback, useRef, useState } from "react";

import type { components } from "@/lib/v1";

import { useBifrostContext } from "./provider";

export interface UseWorkflowState<T> {
  /** Last successful result, or null before the first run. */
  data: T | null;
  /** True while a run is in flight. */
  loading: boolean;
  /** Last error, or null. */
  error: Error | null;
  /** Execute the workflow with `input_data`; resolves to the result. */
  run: (input?: Record<string, unknown>) => Promise<T>;
}

// The generated contract type: `status` is the PascalCase `ExecutionStatus`
// literal union ("Success" | "Failed" | ...), so the compiler enforces the
// exact wire casing in the failed-status check below.
type ExecuteResponse = components["schemas"]["WorkflowExecutionResponse"];

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
      try {
        const resp = await authedFetch("/api/workflows/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: workflowRef,
            input_data: input,
            sync: true,
            // Scope a path::function ref to THIS install's own workflow (so it
            // can't resolve a sibling install's workflow sharing the path).
            ...(appId ? { app_id: appId } : {}),
          }),
        });
        if (!resp.ok) {
          throw new Error(`workflow execution failed: ${resp.status} ${resp.statusText}`);
        }
        const body = (await resp.json()) as ExecuteResponse;
        // A failed run can come back with `error: null` — status is the
        // authoritative signal, the message is best-effort.
        if (body.error || body.status === "Failed") {
          throw new Error(
            body.error ?? `Workflow failed (status: ${body.status})`,
          );
        }
        const result = body.result as T;
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

  return { data, loading, error, run };
}
