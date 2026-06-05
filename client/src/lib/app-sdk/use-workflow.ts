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
 *   - `useWorkflow(idOrName)` → a query-style result you trigger with `run()`.
 *   - `run(input)` POSTs `/api/workflows/execute` with `sync: true` and returns
 *     the workflow `result`.
 */
import { useCallback, useState } from "react";

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

interface ExecuteResponse {
  status: string;
  result?: unknown;
  error?: string | null;
}

/**
 * Run a Bifrost workflow by UUID or name from a v2 app. Must be called within a
 * `<BifrostProvider>` (throws otherwise — same contract as `useBifrostContext`).
 */
export function useWorkflow<T = unknown>(workflowIdOrName: string): UseWorkflowState<T> {
  const { authedFetch } = useBifrostContext();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const run = useCallback(
    async (input: Record<string, unknown> = {}): Promise<T> => {
      setLoading(true);
      setError(null);
      try {
        const resp = await authedFetch("/api/workflows/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workflow_id: workflowIdOrName,
            input_data: input,
            sync: true,
          }),
        });
        if (!resp.ok) {
          throw new Error(`workflow execution failed: ${resp.status} ${resp.statusText}`);
        }
        const body = (await resp.json()) as ExecuteResponse;
        if (body.error) {
          throw new Error(body.error);
        }
        const result = body.result as T;
        setData(result);
        return result;
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [authedFetch, workflowIdOrName],
  );

  return { data, loading, error, run };
}
