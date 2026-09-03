/**
 * Agent Runs API service
 */

import { useEffect } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { webSocketService, type AgentRunUpdate, type AgentRunStepUpdate } from "@/services/websocket";
import { useAgentRunStepStore } from "@/stores/agentRunStepStore";

// Re-export types for new wrappers (added with T8/T9/T15-T17)
export type VerdictRequest = components["schemas"]["VerdictRequest"];
export type VerdictResponse = components["schemas"]["VerdictResponse"];
export type FlagConversation = components["schemas"]["FlagConversationResponse"];
export type SendFlagMessageRequest =
	components["schemas"]["SendFlagMessageRequest"];
export type DryRunRequest = components["schemas"]["DryRunRequest"];
export type DryRunResponse = components["schemas"]["DryRunResponse"];

// ============================================================================
// Types (manual until OpenAPI types are regenerated)
// ============================================================================

export interface AgentRunStep {
	id: string;
	run_id: string;
	step_number: number;
	type: string;
	content: Record<string, unknown> | null;
	tokens_used: number | null;
	duration_ms: number | null;
	created_at: string;
}

export interface AgentRun {
	id: string;
	agent_id: string | null;
	agent_name: string | null;
	trigger_type: string;
	trigger_source: string | null;
	conversation_id: string | null;
	event_delivery_id: string | null;
	input: Record<string, unknown> | null;
	output: Record<string, unknown> | null;
	status: string;
	error: string | null;
	org_id: string | null;
	caller_user_id: string | null;
	caller_email: string | null;
	caller_name: string | null;
	iterations_used: number;
	tokens_used: number;
	budget_max_iterations: number | null;
	budget_max_tokens: number | null;
	duration_ms: number | null;
	llm_model: string | null;
	created_at: string;
	started_at: string | null;
	completed_at: string | null;
	parent_run_id: string | null;
}

export interface AIUsageEntry {
	provider: string;
	model: string;
	input_tokens: number;
	output_tokens: number;
	cost: string | null;
	duration_ms: number | null;
	timestamp: string;
	sequence: number;
}

export interface AIUsageTotals {
	total_input_tokens: number;
	total_output_tokens: number;
	total_cost: string;
	total_duration_ms: number;
	call_count: number;
}

export interface AgentRunDetail extends AgentRun {
	steps: AgentRunStep[];
	child_run_ids: string[];
	ai_usage: AIUsageEntry[] | null;
	ai_totals: AIUsageTotals | null;
}

export interface AgentRunListResponse {
	items: AgentRun[];
	total: number;
	next_cursor: string | null;
}

// ============================================================================
// Hooks
// ============================================================================

export function useAgentRuns(params?: {
	agentId?: string;
	status?: string;
	triggerType?: string;
	orgId?: string;
	startDate?: string;
	endDate?: string;
	/** Full-text search across asked/did/error/caller/metadata (T9). */
	q?: string;
	/** Verdict filter: 'up', 'down', or 'unreviewed' (T9). */
	verdict?: string;
	/**
	 * JSON array of metadata conditions, e.g.
	 * '[{"key":"customer","op":"eq","value":"Acme"}]'. Supported ops:
	 * 'eq' (exact match) and 'contains' (case-insensitive substring).
	 */
	metadataFilter?: string;
	limit?: number;
	offset?: number;
}) {
	return useQuery({
		queryKey: ["agent-runs", params],
		queryFn: async () => {
			const searchParams = new URLSearchParams();
			if (params?.agentId) searchParams.set("agent_id", params.agentId);
			if (params?.status) searchParams.set("status", params.status);
			if (params?.triggerType) searchParams.set("trigger_type", params.triggerType);
			if (params?.orgId) searchParams.set("org_id", params.orgId);
			if (params?.startDate) searchParams.set("start_date", params.startDate);
			if (params?.endDate) searchParams.set("end_date", params.endDate);
			if (params?.q) searchParams.set("q", params.q);
			if (params?.verdict) searchParams.set("verdict", params.verdict);
			if (params?.metadataFilter)
				searchParams.set("metadata_filter", params.metadataFilter);
			if (params?.limit) searchParams.set("limit", String(params.limit));
			if (params?.offset) searchParams.set("offset", String(params.offset));
			const qs = searchParams.toString();
			const url = `/api/agent-runs${qs ? `?${qs}` : ""}`;
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const { data, error } = await apiClient.GET(url as any, {});
			if (error) throw error;
			return data as unknown as AgentRunListResponse;
		},
	});
}

/**
 * Alias for `useAgentRuns` accepting the new search/filter params (T9).
 *
 * Provided for clarity at call sites that want to highlight the search
 * intent (`q` / `verdict` / `metadataFilter`). Same return shape.
 */
export function useSearchAgentRuns(params?: Parameters<typeof useAgentRuns>[0]) {
	return useAgentRuns(params);
}

/**
 * Paginated agent-runs query for list views that need to scroll past the
 * first page. Backend default limit is 50; `pageSize` overrides it. Use
 * `hasNextPage` + `fetchNextPage` from the return value with an
 * `IntersectionObserver` sentinel for infinite scroll.
 *
 * The header count (`total`) comes from the first page and is stable across
 * pages — safe to display while users scroll.
 */
export function useInfiniteAgentRuns(params?: {
	agentId?: string;
	status?: string;
	triggerType?: string;
	orgId?: string;
	startDate?: string;
	endDate?: string;
	q?: string;
	verdict?: string;
	metadataFilter?: string;
	pageSize?: number;
}) {
	const pageSize = params?.pageSize ?? 50;
	return useInfiniteQuery({
		queryKey: ["agent-runs-infinite", { ...params, pageSize }],
		initialPageParam: 0,
		queryFn: async ({ pageParam }) => {
			const searchParams = new URLSearchParams();
			if (params?.agentId) searchParams.set("agent_id", params.agentId);
			if (params?.status) searchParams.set("status", params.status);
			if (params?.triggerType) searchParams.set("trigger_type", params.triggerType);
			if (params?.orgId) searchParams.set("org_id", params.orgId);
			if (params?.startDate) searchParams.set("start_date", params.startDate);
			if (params?.endDate) searchParams.set("end_date", params.endDate);
			if (params?.q) searchParams.set("q", params.q);
			if (params?.verdict) searchParams.set("verdict", params.verdict);
			if (params?.metadataFilter)
				searchParams.set("metadata_filter", params.metadataFilter);
			searchParams.set("limit", String(pageSize));
			searchParams.set("offset", String(pageParam));
			const url = `/api/agent-runs?${searchParams.toString()}`;
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const { data, error } = await apiClient.GET(url as any, {});
			if (error) throw error;
			return data as unknown as AgentRunListResponse;
		},
		getNextPageParam: (lastPage, allPages) => {
			const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
			return loaded < lastPage.total ? loaded : undefined;
		},
	});
}

export function useAgentRun(runId: string | undefined, options?: { refetchInterval?: number | false | ((query: { state: { data: AgentRunDetail | undefined } }) => number | false) }) {
	return useQuery({
		queryKey: ["agent-runs", runId],
		queryFn: async () => {
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const { data, error } = await apiClient.GET(`/api/agent-runs/${runId}` as any, {});
			if (error) throw error;
			return data as unknown as AgentRunDetail;
		},
		enabled: !!runId,
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		refetchInterval: options?.refetchInterval as any,
	});
}

/**
 * Hook for real-time agent run list updates via WebSocket.
 * Updates React Query cache in-place (no refetch) when updates arrive.
 */
export function useAgentRunListStream(options: { enabled?: boolean } = {}) {
	const { enabled = true } = options;
	const queryClient = useQueryClient();

	useEffect(() => {
		if (!enabled) return;

		let unsubscribe: (() => void) | null = null;

		const init = async () => {
			try {
				await webSocketService.connect(["agent-runs"]);
				unsubscribe = webSocketService.onAgentRunUpdate(
					(update: AgentRunUpdate) => {
						const buildNewRun = (): AgentRun => ({
							id: update.run_id,
							agent_id: update.agent_id,
							agent_name: update.agent_name,
							trigger_type: update.trigger_type,
							status: update.status,
							iterations_used: update.iterations_used,
							tokens_used: update.tokens_used,
							duration_ms: update.duration_ms ?? null,
							error: update.error ?? null,
							org_id: update.org_id ?? null,
							trigger_source: null,
							conversation_id: null,
							event_delivery_id: null,
							input: null,
							output: null,
							caller_user_id: null,
							caller_email: null,
							caller_name: null,
							budget_max_iterations: null,
							budget_max_tokens: null,
							llm_model: null,
							created_at: update.timestamp,
							started_at: update.started_at ?? update.timestamp,
							completed_at: update.completed_at ?? null,
							parent_run_id: null,
						});

						const patchItems = (items: AgentRun[]): { items: AgentRun[]; appended: boolean } => {
							const existingIndex = items.findIndex(
								(run) => run.id === update.run_id,
							);
							if (existingIndex >= 0) {
								const next = [...items];
								next[existingIndex] = {
									...next[existingIndex],
									status: update.status,
									iterations_used: update.iterations_used,
									tokens_used: update.tokens_used,
									duration_ms: update.duration_ms ?? next[existingIndex].duration_ms,
									error: update.error ?? next[existingIndex].error,
								};
								return { items: next, appended: false };
							}
							return { items: [buildNewRun(), ...items], appended: true };
						};

						// Flat list caches (legacy `useAgentRuns`).
						const flatCaches = queryClient.getQueriesData<AgentRunListResponse>({
							queryKey: ["agent-runs"],
						});
						flatCaches.forEach(([queryKey, oldData]) => {
							if (!oldData?.items) return;
							const { items, appended } = patchItems(oldData.items);
							queryClient.setQueryData(queryKey, {
								...oldData,
								items,
								total: appended ? oldData.total + 1 : oldData.total,
							});
						});

						// Infinite list caches (`useInfiniteAgentRuns`). Only patch the
						// first page — new runs prepend there; in-place updates target
						// whichever page the run currently lives on.
						type InfiniteData = { pages: AgentRunListResponse[]; pageParams: unknown[] };
						const infiniteCaches = queryClient.getQueriesData<InfiniteData>({
							queryKey: ["agent-runs-infinite"],
						});
						infiniteCaches.forEach(([queryKey, oldData]) => {
							if (!oldData?.pages?.length) return;
							const pageIdx = oldData.pages.findIndex((p) =>
								p.items.some((r) => r.id === update.run_id),
							);
							if (pageIdx >= 0) {
								const nextPages = [...oldData.pages];
								const { items } = patchItems(nextPages[pageIdx].items);
								nextPages[pageIdx] = { ...nextPages[pageIdx], items };
								queryClient.setQueryData(queryKey, {
									...oldData,
									pages: nextPages,
								});
							} else {
								// New run — prepend to first page; bump totals.
								const nextPages = [...oldData.pages];
								const first = nextPages[0];
								nextPages[0] = {
									...first,
									items: [buildNewRun(), ...first.items],
									total: first.total + 1,
								};
								queryClient.setQueryData(queryKey, {
									...oldData,
									pages: nextPages,
								});
							}
						});
					},
				);
			} catch (error) {
				console.error("[useAgentRunListStream] Failed to connect:", error);
			}
		};

		init();

		return () => {
			if (unsubscribe) unsubscribe();
			webSocketService.unsubscribe("agent-runs");
		};
	}, [enabled, queryClient]);
}

/**
 * Hook for real-time agent run detail updates via WebSocket.
 * Steps are buffered in Zustand store (agentRunStepStore) and merged at
 * render time in the component — this avoids race conditions between
 * the initial API fetch and WebSocket connection.
 */
export function useAgentRunStream(
	runId: string | undefined,
	options: { enabled?: boolean; onComplete?: (runId: string) => void } = {},
) {
	const { enabled = true, onComplete } = options;
	const queryClient = useQueryClient();

	useEffect(() => {
		if (!enabled || !runId) return;

		const store = useAgentRunStepStore.getState();
		store.startStreaming(runId);

		let unsubUpdate: (() => void) | null = null;
		let unsubStep: (() => void) | null = null;

		const init = async () => {
			try {
				const channel = `agent-run:${runId}`;
				await webSocketService.connect([channel]);

				store.setConnectionStatus(runId, true);

				// Listen for status updates — update run metadata in React Query cache
				unsubUpdate = webSocketService.onAgentRunUpdate(
					(update: AgentRunUpdate) => {
						if (update.run_id !== runId) return;
						queryClient.setQueryData<AgentRunDetail>(
							["agent-runs", runId],
							(old) => {
								if (!old) return old;
								return {
									...old,
									status: update.status,
									iterations_used: update.iterations_used,
									tokens_used: update.tokens_used,
									duration_ms: update.duration_ms,
									error: update.error,
								};
							},
						);

						// On terminal status, refetch full data and notify
						const isTerminal = ["completed", "failed", "budget_exceeded", "timeout", "cancelled"].includes(update.status);
						if (isTerminal) {
							queryClient.invalidateQueries({ queryKey: ["agent-runs", runId] });
							onComplete?.(runId);
						}
					},
				);

				// Listen for new steps — buffer in Zustand store, NOT React Query cache
				unsubStep = webSocketService.onAgentRunStep(
					runId,
					(update: AgentRunStepUpdate) => {
						const step = { ...update.step, created_at: update.timestamp };
						useAgentRunStepStore.getState().appendStep(runId, step);
					},
				);
			} catch (error) {
				console.error("[useAgentRunStream] Failed to connect:", error);
			}
		};

		init();

		return () => {
			if (unsubUpdate) unsubUpdate();
			if (unsubStep) unsubStep();
			if (runId) {
				webSocketService.unsubscribe(`agent-run:${runId}`);
				useAgentRunStepStore.getState().clearStream(runId);
			}
		};
	}, [runId, enabled, queryClient, onComplete]);
}

// ============================================================================
// Verdict, flag-conversation, dry-run, regenerate-summary (T15-T17)
// ============================================================================

/**
 * Set a verdict (`up` / `down`) on a completed run.
 *
 * Records an audit row server-side. Caller is responsible for invalidating
 * the run detail cache (`["get", "/api/agent-runs/{run_id}", ...]`) and the
 * list cache (`["agent-runs", ...]`) on success if needed.
 */
export function useSetVerdict() {
	return $api.useMutation("post", "/api/agent-runs/{run_id}/verdict");
}

/** Clear the verdict on a run. Records an audit row server-side. */
export function useClearVerdict() {
	return $api.useMutation("delete", "/api/agent-runs/{run_id}/verdict");
}

/**
 * Re-run an agent run with its original input (server enqueues a new run,
 * returns the new `run_id`).
 */
export function useRerunAgentRun() {
	return $api.useMutation("post", "/api/agent-runs/{run_id}/rerun");
}

/**
 * Fetch the tuning conversation attached to a flagged run.
 *
 * Server creates an empty conversation row if none exists yet, so the UI
 * can stream messages into a stable `id`.
 */
export function useFlagConversation(runId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/{run_id}/flag-conversation",
		{ params: { path: { run_id: runId ?? "" } } },
		{ enabled: !!runId },
	);
}

/** Append a user turn and synchronously get the tuning-model reply. */
export function useSendFlagMessage() {
	return $api.useMutation(
		"post",
		"/api/agent-runs/{run_id}/flag-conversation/message",
	);
}

/**
 * Single-run dry-run of a proposed system prompt against a past run's
 * transcript. One LLM call — does not re-execute tools.
 */
export function useDryRunAgent() {
	return $api.useMutation("post", "/api/agent-runs/{run_id}/dry-run");
}

/** Reset summary state and re-enqueue a summarization job. Admin-only. */
export function useRegenerateSummary() {
	return $api.useMutation(
		"post",
		"/api/agent-runs/{run_id}/regenerate-summary",
	);
}

/** Kick off a bulk summary backfill (dry_run supported). Admin-only. */
export function useBackfillSummaries() {
	return $api.useMutation("post", "/api/agent-runs/backfill-summaries");
}

/** Fetch the current state of a single backfill job. Admin-only. */
export function useSummaryBackfillJob(jobId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/backfill-jobs/{job_id}",
		{ params: { path: { job_id: jobId ?? "" } } },
		{ enabled: !!jobId },
	);
}

/**
 * List summary backfill jobs. When `activeOnly` is true, returns only
 * running jobs — used on page mount to re-attach progress UI to an
 * already-running job.
 */
export function useSummaryBackfillJobs(activeOnly: boolean = false) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/backfill-jobs",
		{ params: { query: { active: activeOnly } } },
	);
}

/** Cancel a stuck or unwanted backfill job. Admin-only. */
export function useCancelBackfillJob() {
	return $api.useMutation(
		"post",
		"/api/agent-runs/backfill-jobs/{job_id}/cancel",
	);
}

/**
 * Cheap preview — returns eligible count + cost estimate for the given
 * scope. Used to decide whether to render the Backfill button at all
 * (hide when eligible=0 to avoid dead-end "Nothing to backfill" modals).
 *
 * Three orthogonal scopes:
 *   - default: pending + failed summaries.
 *   - `promptVersionBelow="vN"`: also counts completed runs on an older
 *     prompt version (or NULL).
 *   - `includeCompleted=true`: counts ALL completed summaries regardless
 *     of version. Overrides the version filter.
 * Admin-only.
 */
export function useBackfillEligible(
	agentId?: string,
	promptVersionBelow?: string,
	includeCompleted?: boolean,
) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/backfill-eligible",
		{
			params: {
				query: {
					agent_id: agentId,
					prompt_version_below: promptVersionBelow,
					include_completed: includeCompleted,
				},
			},
		},
	);
}

/**
 * Distinct top-level keys present in `run_metadata` for the given agent's
 * runs. Powers the key combobox on the captured-data filter.
 */
export function useMetadataKeys(agentId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/metadata-keys",
		{ params: { query: { agent_id: agentId ?? "" } } },
		{ enabled: !!agentId },
	);
}

/**
 * Distinct values observed for `key` in `run_metadata` for the given
 * agent's runs. Powers the value combobox when the user picks 'eq'.
 */
export function useMetadataValues(
	agentId: string | undefined,
	key: string | undefined,
) {
	return $api.useQuery(
		"get",
		"/api/agent-runs/metadata-values",
		{
			params: { query: { agent_id: agentId ?? "", key: key ?? "" } },
		},
		{ enabled: !!agentId && !!key },
	);
}
