/**
 * Workers API service
 *
 * Provides hooks for:
 * - Listing process pools and their status
 * - Getting pool details
 * - Recycling worker processes
 * - Queue status
 * - Pool statistics
 *
 * Uses authFetch because worker controls predate the generated client wrapper.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// =============================================================================
// Worker Contracts
// =============================================================================

// Process states from ProcessPoolManager: idle, busy, killed
export type ProcessState = "idle" | "busy" | "killed";

export type ProcessInfo = components["schemas"]["ProcessInfo"];

export type PoolSummary = components["schemas"]["PoolSummary"];
export type PoolDetail = components["schemas"]["PoolDetail"];
export type PoolsListResponse = components["schemas"]["PoolsListResponse"];
export type WorkerIncarnation = components["schemas"]["PlatformWorkerIncarnation"];
export type WorkerFormCapacity = components["schemas"]["PlatformWorkerFormCapacity"];
export type WorkerCapacityOverview = components["schemas"]["PlatformWorkerCapacityOverview"];

export interface PoolStatsResponse {
	total_pools: number;
	total_processes: number;
	total_idle: number;
	total_busy: number;
}

export interface QueueItem {
	execution_id: string;
	position: number;
	queued_at: string | null;
}

export interface QueueStatusResponse {
	total: number;
	items: QueueItem[];
}

export interface RecycleRequest {
	reason?: string;
}

export interface RecycleResponse {
	success: boolean;
	message: string;
	worker_id: string;
	process_id: string | null;
	pid: number | null;
}

export interface RecycleAllRequest {
	reason?: string;
}

export interface RecycleAllResponse {
	success: boolean;
	message: string;
	worker_id: string;
	processes_affected: number;
}

// =============================================================================
// Pool Hooks
// =============================================================================

/**
 * Hook to fetch all process pools
 */
export async function getWorkers(options?: { includeIncarnations?: boolean }): Promise<PoolsListResponse> {
	const query = options?.includeIncarnations ? "?include_incarnations=true" : "";
	const response = await authFetch(`/api/platform/workers${query}`);
	if (!response.ok) throw new Error(`Failed to fetch pools: ${response.statusText}`);
	return response.json();
}

export function usePools(options?: { includeIncarnations?: boolean }) {
	return useQuery<PoolsListResponse>({
		queryKey: ["pools", options?.includeIncarnations === true],
		queryFn: () => getWorkers(options),
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to fetch a single pool's details
 */
export function usePool(workerId: string) {
	return useQuery<PoolDetail>({
		queryKey: ["pools", workerId],
		queryFn: async () => {
			const response = await authFetch(`/api/platform/workers/${workerId}`);
			if (!response.ok) {
				throw new Error(`Failed to fetch pool: ${response.statusText}`);
			}
			return response.json();
		},
		enabled: !!workerId,
	});
}

/**
 * Hook to fetch pool statistics
 */
export function usePoolStats() {
	return useQuery<PoolStatsResponse>({
		queryKey: ["pools", "stats"],
		queryFn: async () => {
			const response = await authFetch("/api/platform/workers/stats");
			if (!response.ok) {
				throw new Error(`Failed to fetch pool stats: ${response.statusText}`);
			}
			return response.json();
		},
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to recycle a process in a pool
 */
export function useRecycleProcess() {
	const queryClient = useQueryClient();

	return useMutation<
		RecycleResponse,
		Error,
		{ params: { path: { worker_id: string; pid: number } }; body: RecycleRequest }
	>({
		mutationFn: async ({ params, body }) => {
			const { worker_id, pid } = params.path;
			const response = await authFetch(
				`/api/platform/workers/${worker_id}/processes/${pid}/recycle`,
				{
					method: "POST",
					body: JSON.stringify(body),
				}
			);
			if (!response.ok) {
				throw new Error(`Failed to recycle process: ${response.statusText}`);
			}
			return response.json();
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["pools"] });
		},
	});
}

// =============================================================================
// Queue Hooks
// =============================================================================

/**
 * Hook to fetch queue status
 */
export function useQueueStatus(params?: { limit?: number; offset?: number }) {
	return useQuery<QueueStatusResponse>({
		queryKey: ["queue", params],
		queryFn: async () => {
			const searchParams = new URLSearchParams();
			if (params?.limit) searchParams.set("limit", String(params.limit));
			if (params?.offset) searchParams.set("offset", String(params.offset));

			const url = `/api/platform/queue${searchParams.toString() ? `?${searchParams}` : ""}`;
			const response = await authFetch(url);
			if (!response.ok) {
				throw new Error(`Failed to fetch queue: ${response.statusText}`);
			}
			return response.json();
		},
		// No polling - real-time updates come via WebSocket (useWorkerWebSocket)
	});
}

/**
 * Hook to recycle all processes in a pool
 */
export function useRecycleAllProcesses() {
	const queryClient = useQueryClient();

	return useMutation<
		RecycleAllResponse,
		Error,
		{ workerId: string; reason?: string }
	>({
		mutationFn: async ({ workerId, reason }) => {
			const response = await authFetch(
				`/api/platform/workers/${workerId}/recycle-all`,
				{
					method: "POST",
					body: JSON.stringify({ reason }),
				}
			);
			if (!response.ok) {
				const error = await response.json().catch(() => ({}));
				throw new Error(
					error.detail || `Failed to recycle: ${response.statusText}`
				);
			}
			return response.json();
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["pools"] });
		},
	});
}

// =============================================================================
// Worker Metrics (Time-Series for Diagnostics Chart)
// =============================================================================

export type WorkerMetricPoint = components["schemas"]["WorkerMetricPoint"];
export type WorkerMetricsResponse = components["schemas"]["WorkerMetricsResponse"];

export function useWorkerMetrics(range: string = "1h") {
    return useQuery<WorkerMetricsResponse>({
        queryKey: ["worker-metrics", range],
        queryFn: async () => {
            const response = await authFetch(
                `/api/platform/workers/metrics?range=${range}`
            );
            if (!response.ok) {
                throw new Error(
                    `Failed to fetch worker metrics: ${response.statusText}`
                );
            }
            return response.json();
        },
        refetchInterval: 60_000, // Refresh every 60s to get new data points
    });
}

