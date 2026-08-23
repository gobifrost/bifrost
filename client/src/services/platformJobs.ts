import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformJob = components["schemas"]["PlatformJobPublic"];
export type PlatformJobCancelResponse =
	components["schemas"]["PlatformJobCancelResponse"];

export async function getPlatformJobs(
	options: {
		activeOnly?: boolean;
		limit?: number;
		signal?: AbortSignal;
	} = {},
): Promise<PlatformJob[]> {
	const { data, error } = await apiClient.GET("/api/platform-jobs", {
		params: {
			query: {
				active_only: options.activeOnly ?? true,
				limit: options.limit ?? 50,
			},
		},
		signal: options.signal,
	});
	if (error) {
		throw new Error("Failed to load platform jobs");
	}
	return data.jobs;
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
