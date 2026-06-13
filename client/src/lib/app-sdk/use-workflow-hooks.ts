/**
 * `useWorkflowQuery` / `useWorkflowMutation` — the v2 SDK's PRIMARY workflow
 * hooks, shaped after React Query (which every developer — and every LLM — knows
 * cold). They make the read-vs-write intent explicit at the call site:
 *
 *   - **useWorkflowQuery(ref, params?)** — a READ. Auto-runs on mount, re-runs
 *     when the ref/params change, and exposes `refresh()`. Use for "load this
 *     data when the component shows" (`{ data, loading, error, refresh }`).
 *   - **useWorkflowMutation(ref)** — a WRITE/ACTION. Does NOT run on mount; you
 *     call `mutate(input)` from an event handler (`{ mutate, data, loading,
 *     error }`).
 *
 * Why these are primary, not the lower-level `useWorkflow`: `useWorkflow` returns
 * `data` but never runs until you call `run()`, so `const { data } = useWorkflow(...)`
 * silently stays null — a trap. The query/mutation split removes that ambiguity.
 * Both are thin wrappers over `useWorkflow`, so they share its auth/transport,
 * `app_id` scoping, and stale-run guarding. `useWorkflow` remains exported as the
 * low-level escape hatch.
 */
import { useEffect } from "react";

import { useWorkflow, type UseWorkflowState } from "./use-workflow";

export interface UseWorkflowQueryState<T> {
  /** Last successful result, or null before the first run completes. */
  data: T | null;
  /** True while a run is in flight (including the auto-run on mount). */
  loading: boolean;
  /** Last error, or null. */
  error: Error | null;
  /** Re-run the query (e.g. a refresh button). Resolves to the new result. */
  refresh: (input?: Record<string, unknown>) => Promise<T>;
}

export interface UseWorkflowMutationState<T> {
  /** Trigger the workflow imperatively; resolves to the result. */
  mutate: (input?: Record<string, unknown>) => Promise<T>;
  /** Last successful result, or null before the first mutate. */
  data: T | null;
  /** True while a mutate is in flight. */
  loading: boolean;
  /** Last error, or null. */
  error: Error | null;
}

/**
 * Read a workflow's result, React-Query style: runs on mount and whenever
 * ``workflowRef`` or the stable-serialized ``params`` change. ``params`` is the
 * ``input_data`` for the run. Returns ``refresh`` to re-run on demand.
 */
export function useWorkflowQuery<T = unknown>(
  workflowRef: string,
  params?: Record<string, unknown>,
): UseWorkflowQueryState<T> {
  const { data, loading, error, run }: UseWorkflowState<T> = useWorkflow<T>(workflowRef);

  // Serialize params for a stable effect dep without re-running on every render
  // (a fresh object literal each render would otherwise loop). JSON is enough —
  // params are plain JSON input_data.
  const paramsKey = params ? JSON.stringify(params) : "";

  useEffect(() => {
    run(params ?? {}).catch(() => {
      // Error is captured in the hook's `error` state by `run`; swallow the
      // rejection here so an auto-run failure doesn't become an unhandled
      // promise rejection.
    });
    // `run` is memoized on its transport deps; re-run when it or params change.
    // `params` is intentionally read via the stable `paramsKey` serialization.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, paramsKey]);

  return { data, loading, error, refresh: run };
}

/**
 * Trigger a workflow imperatively (an action/write). Nothing runs until
 * ``mutate`` is called — the right shape for "do X on click/submit".
 */
export function useWorkflowMutation<T = unknown>(
  workflowRef: string,
): UseWorkflowMutationState<T> {
  const { data, loading, error, run }: UseWorkflowState<T> = useWorkflow<T>(workflowRef);
  return { mutate: run, data, loading, error };
}
