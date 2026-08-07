import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type SchedulerDiagnosticsResponse =
	components["schemas"]["SchedulerDiagnosticsResponse"];
export type SchedulerTaskHistoryResponse =
	components["schemas"]["SchedulerTaskHistoryResponse"];
export type SchedulerTaskStatus = components["schemas"]["SchedulerTaskStatus"];

export async function getSchedulerDiagnostics(
	options: { signal?: AbortSignal } = {},
): Promise<SchedulerDiagnosticsResponse> {
	const { data, error } = await apiClient.GET("/api/platform/scheduler", {
		signal: options.signal,
	});
	if (error) {
		throw new Error("Failed to load scheduler diagnostics");
	}
	return data;
}

export async function getSchedulerTaskHistory(
	taskId: string,
	options: { signal?: AbortSignal; limit?: number } = {},
): Promise<SchedulerTaskHistoryResponse> {
	const { data, error } = await apiClient.GET(
		"/api/platform/scheduler/tasks/{task_id}/runs",
		{
			params: {
				path: { task_id: taskId },
				query: { limit: options.limit ?? 10 },
			},
			signal: options.signal,
		},
	);
	if (error) {
		throw new Error("Failed to load scheduled task history");
	}
	return data;
}
