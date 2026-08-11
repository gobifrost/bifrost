import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformJob = components["schemas"]["PlatformJobPublic"];
export type PlatformJobCancelResponse =
	components["schemas"]["PlatformJobCancelResponse"];

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
