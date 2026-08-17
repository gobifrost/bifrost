import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformJob = components["schemas"]["PlatformJobPublic"];
export type PlatformJobCancelResponse =
	components["schemas"]["PlatformJobCancelResponse"];
export type PlatformJobListResponse =
	components["schemas"]["PlatformJobListResponse"];

async function platformJobError(
	response: Response,
	fallback: string,
): Promise<Error> {
	const body = await response.json().catch(() => null);
	const detail =
		body && typeof body === "object" && typeof body.detail === "string"
			? body.detail
			: fallback;
	return new Error(detail);
}

export async function getPlatformJob(
	jobId: string,
	signal?: AbortSignal,
): Promise<PlatformJob> {
	const response = await authFetch(`/api/platform-jobs/${jobId}`, { signal });
	if (!response.ok) {
		throw await platformJobError(response, "Failed to load build progress");
	}
	return (await response.json()) as PlatformJob;
}

export async function listPlatformJobs(
	options: {
		activeOnly?: boolean;
		limit?: number;
		signal?: AbortSignal;
	} = {},
): Promise<PlatformJob[]> {
	const query = new URLSearchParams({
		active_only: String(options.activeOnly ?? true),
		limit: String(options.limit ?? 50),
	});
	const response = await authFetch(`/api/platform-jobs?${query.toString()}`, {
		signal: options.signal,
	});
	if (!response.ok) {
		throw await platformJobError(response, "Failed to load platform jobs");
	}
	const body = (await response.json()) as PlatformJobListResponse;
	return body.jobs;
}

export async function cancelPlatformJob(
	jobId: string,
): Promise<PlatformJobCancelResponse> {
	const response = await authFetch(`/api/platform-jobs/${jobId}/cancel`, {
		method: "POST",
	});
	if (!response.ok) {
		throw await platformJobError(response, "Failed to cancel build");
	}
	return (await response.json()) as PlatformJobCancelResponse;
}
