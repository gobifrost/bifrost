import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformJob = components["schemas"]["PlatformJobPublic"];
export type PlatformJobListResponse =
	components["schemas"]["PlatformJobListResponse"];
export type PlatformJobCancelResponse =
	components["schemas"]["PlatformJobCancelResponse"];

export async function getPlatformJobs(
	options: {
		activeOnly?: boolean;
		limit?: number;
		offset?: number;
		status?: PlatformJob["status"];
		search?: string;
		signal?: AbortSignal;
	} = {},
): Promise<PlatformJobListResponse> {
	const { data, error } = await apiClient.GET("/api/platform-jobs", {
		params: {
			query: {
				active_only: options.activeOnly ?? true,
				limit: options.limit ?? 50,
				offset: options.offset ?? 0,
				status: options.status,
				search: options.search,
			},
		},
		signal: options.signal,
	});
	if (error) {
		throw new Error("Failed to load platform jobs");
	}
	return data;
}

export async function cancelPlatformJob(
	jobId: string,
): Promise<PlatformJobCancelResponse> {
	const { data, error } = await apiClient.POST(
		"/api/platform-jobs/{job_id}/cancel",
		{
			params: { path: { job_id: jobId } },
		},
	);
	if (error) {
		throw new Error("Failed to cancel platform job");
	}
	return data;
}
