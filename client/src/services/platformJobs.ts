import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PlatformJob = components["schemas"]["PlatformJobPublic"];
export type PlatformJobListResponse =
	components["schemas"]["PlatformJobListResponse"];
export type PlatformJobCancelResponse =
	components["schemas"]["PlatformJobCancelResponse"];

const TERMINAL_PLATFORM_JOB_STATUSES = new Set<PlatformJob["status"]>([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);
const PLATFORM_JOB_OBSERVATION_ATTEMPTS = 3;
const PLATFORM_JOB_OBSERVATION_RETRY_MS = 100;

export type PlatformJobObservation = {
	promise: Promise<PlatformJob | undefined>;
	cancel: () => void;
};

export async function getPlatformJob(jobId: string): Promise<PlatformJob> {
	const { data, error } = await apiClient.GET("/api/platform-jobs/{job_id}", {
		params: { path: { job_id: jobId } },
	});
	if (error) {
		throw new Error("Failed to load platform job");
	}
	return data;
}

/**
 * Briefly recover a shared PlatformJob status when a notification is missed.
 *
 * Browser progress remains notification-driven. This is deliberately bounded
 * shared recovery (not a feature-specific polling loop), so callers always
 * receive a terminal result or a deterministic observation error.
 */
export function observePlatformJob(
	jobId: string,
	onUpdate: (job: PlatformJob) => void,
): PlatformJobObservation {
	let cancelled = false;
	const cancel = () => {
		cancelled = true;
	};
	const promise = (async (): Promise<PlatformJob | undefined> => {
		let lastError: unknown;
		for (let attempt = 0; attempt < PLATFORM_JOB_OBSERVATION_ATTEMPTS; attempt++) {
			if (cancelled) return undefined;
			try {
				const job = await getPlatformJob(jobId);
				if (cancelled) return undefined;
				onUpdate(job);
				if (TERMINAL_PLATFORM_JOB_STATUSES.has(job.status)) return job;
			} catch (error) {
				lastError = error;
			}
			if (attempt < PLATFORM_JOB_OBSERVATION_ATTEMPTS - 1) {
				await new Promise<void>((resolve) =>
					setTimeout(resolve, PLATFORM_JOB_OBSERVATION_RETRY_MS),
				);
			}
		}
		if (cancelled) return undefined;
		throw lastError ?? new Error("Timed out observing platform job status");
	})();
	return { promise, cancel };
}

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
