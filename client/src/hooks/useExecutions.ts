/**
 * React Query hooks for workflow executions
 * Uses openapi-react-query for type-safe API access
 */

import { $api, apiClient } from "@/lib/api-client";
import type { ExecutionFilters } from "@/lib/client-types";
import { sameQueryParamsExcept } from "@/lib/paginated-query";
import { useQueryClient } from "@tanstack/react-query";

// Re-export types for convenience
export type { ExecutionFilters };

/**
 * Hook to fetch executions with optional organization filtering
 * @param filterScope - Filter scope: undefined = all, null = global only, string = org UUID
 * @param filters - Additional execution filters
 * @param continuationToken - Pagination token
 *
 * The scope query param controls filtering:
 * - Omitted (undefined): show all executions (superusers) / user's org (org users)
 * - "global": show only global executions (org_id IS NULL) - not commonly used
 * - UUID string: show that org's executions only
 */
export function useExecutions(
	filterScope?: string | null,
	filters?: ExecutionFilters,
	continuationToken?: string,
	options: { preservePageData?: boolean } = {},
) {
	// Build query params
	const queryParams: Record<string, string> = {};
	// Convert filterScope to scope param
	if (filterScope === null) {
		queryParams["scope"] = "global";
	} else if (filterScope !== undefined) {
		queryParams["scope"] = filterScope;
	}
	// undefined = don't send scope (show all)

	if (filters?.workflow_id) queryParams["workflowId"] = filters.workflow_id;
	else if (filters?.workflow_name)
		queryParams["workflow_name"] = filters.workflow_name;
	if (filters?.status) queryParams["status"] = filters.status;
	if (filters?.start_date) queryParams["startDate"] = filters.start_date;
	if (filters?.end_date) queryParams["endDate"] = filters.end_date;
	if (filters?.limit) queryParams["limit"] = filters.limit.toString();
	if (continuationToken) queryParams["continuationToken"] = continuationToken;

	return $api.useQuery(
		"get",
		"/api/executions",
		{
			params: { query: queryParams },
		},
		{
			placeholderData: options.preservePageData
				? (previousData, previousQuery) =>
						sameQueryParamsExcept(queryParams, previousQuery, [
							"continuationToken",
						])
							? previousData
							: undefined
				: undefined,
		},
	);
}

/**
 * Whether a run read failed because the run does not exist: it was removed by
 * run retention, never existed, or is outside the caller's access. Matches an
 * Error whose message carries the 404 status and a FastAPI `{ detail }` body
 * saying "not found".
 */
export function isNotFoundError(error: unknown): boolean {
	if (error instanceof Error && error.message.includes("404")) {
		return true;
	}
	if (error && typeof error === "object" && "detail" in error) {
		const detail = (error as Record<string, unknown>).detail;
		return (
			typeof detail === "string" &&
			detail.toLowerCase().includes("not found")
		);
	}
	return false;
}

/**
 * Hook to fetch a single execution by ID
 * @param executionId - The execution ID to fetch
 * @param options - Options object with optional disablePolling flag
 *
 * Polling behavior:
 * - Polls every 2s while execution is Pending/Running and polling is not disabled
 * - Pass disablePolling: true when WebSocket is connected and execution is running
 *   to avoid duplicate requests
 */
export function useExecution(
	executionId: string | undefined,
	options: { disablePolling?: boolean } = {},
) {
	const { disablePolling = false } = options;

	return $api.useQuery(
		"get",
		"/api/executions/{execution_id}",
		{ params: { path: { execution_id: executionId! } } },
		{
			enabled: !!executionId,
			// Keep data fresh for 5 seconds to avoid duplicate requests
			// (e.g., from React Strict Mode double-mounting)
			staleTime: 5000,
			// Retry 404s briefly (Redis-first architecture): the execution may be
			// pending in Redis but not yet in PostgreSQL. Five retries at 2s.
			retry: (failureCount, error) =>
				isNotFoundError(error) && failureCount < 5,
			retryDelay: 2000,
			refetchInterval: (query) => {
				// Disable polling if WebSocket is handling updates
				if (disablePolling) {
					return false;
				}
				// A removed run stays removed.
				if (query.state.error && isNotFoundError(query.state.error)) {
					return false;
				}
				// Poll while waiting for the worker to create the record, but
				// not once the read has failed.
				if (!query.state.data) {
					return query.state.error ? false : 2000;
				}
				const status = query.state.data.status;
				return status === "Pending" || status === "Running"
					? 2000
					: false;
			},
		},
	);
}

/**
 * Progressive loading hook: Get only execution result
 */
export function useExecutionResult(
	executionId: string | undefined,
	enabled = true,
) {
	return $api.useQuery(
		"get",
		"/api/executions/{execution_id}/result",
		{ params: { path: { execution_id: executionId! } } },
		{ enabled: !!executionId && enabled },
	);
}

/**
 * Progressive loading hook: Get only execution logs (admin only)
 */
export function useExecutionLogs(
	executionId: string | undefined,
	enabled = true,
) {
	return $api.useQuery(
		"get",
		"/api/executions/{execution_id}/logs",
		{ params: { path: { execution_id: executionId! } } },
		{
			enabled: !!executionId && enabled,
			// Logs don't change once execution is complete
			staleTime: 30000,
		},
	);
}

/**
 * Progressive loading hook: Get only execution variables (admin only)
 */
export function useExecutionVariables(
	executionId: string | undefined,
	enabled = true,
) {
	return $api.useQuery(
		"get",
		"/api/executions/{execution_id}/variables",
		{ params: { path: { execution_id: executionId! } } },
		{ enabled: !!executionId && enabled },
	);
}

/**
 * Mutation hook for canceling an execution
 */
export function useCancelExecution() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/executions/{execution_id}/cancel", {
		onSuccess: (_, variables) => {
			const executionId = variables.params.path.execution_id;
			// Invalidate the specific execution query
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/executions/{execution_id}",
					{ params: { path: { execution_id: executionId } } },
				],
			});
			// Also invalidate the executions list
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/executions"],
			});
		},
	});
}

// ============================================================================
// Imperative functions for non-hook usage (polling, etc.)
// ============================================================================

/**
 * Fetch a single execution imperatively (for polling/non-hook contexts)
 */
export async function getExecution(executionId: string) {
	const { data, error } = await apiClient.GET(
		"/api/executions/{execution_id}",
		{ params: { path: { execution_id: executionId } } },
	);
	if (error) throw new Error(`Failed to fetch execution: ${error}`);
	return data!;
}

/**
 * Fetch execution variables imperatively
 */
export async function getExecutionVariables(executionId: string) {
	const { data, error } = await apiClient.GET(
		"/api/executions/{execution_id}/variables",
		{ params: { path: { execution_id: executionId } } },
	);
	if (error) throw new Error(`Failed to fetch execution variables: ${error}`);
	return data as Record<string, unknown>;
}

/**
 * Cancel an execution imperatively
 */
export async function cancelExecution(executionId: string) {
	const { data, error } = await apiClient.POST(
		"/api/executions/{execution_id}/cancel",
		{ params: { path: { execution_id: executionId } } },
	);
	if (error) throw new Error(`Failed to cancel execution: ${error}`);
	return data;
}
