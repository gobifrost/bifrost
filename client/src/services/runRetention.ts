import { useQuery } from "@tanstack/react-query";

import { authFetch } from "@/lib/api-client";
import { ApiError, parseApiError } from "@/lib/api-error";
import type { components } from "@/lib/v1";

export type RunRetentionStatus = components["schemas"]["RunRetentionStatus"];
export type RunRetentionSettingsUpdate =
	components["schemas"]["RunRetentionSettingsUpdate"];
export type RunRetentionPreview = components["schemas"]["RunRetentionPreview"];
export type RunRetentionPublic = components["schemas"]["RunRetentionPublic"];
export type PlatformJobAccepted = components["schemas"]["PlatformJobAccepted"];

/**
 * The fields of a dry run's result that the settings card shows. The job's
 * `result` is untyped in the OpenAPI schema; the server builds it in
 * `api/src/services/run_retention/deleter.py::plan_run_retention`.
 */
export interface RunRetentionPlan {
	workflow_runs: number;
	agent_runs: number;
	events: number;
}

/** The server's reason for a refused request, with its status code. */
async function requestError(response: Response): Promise<ApiError> {
	const body = await response.json().catch(() => null);
	return body && typeof body === "object" && "detail" in body
		? parseApiError(body, response.status)
		: new ApiError(
				`Run retention request failed: ${response.statusText}`,
				response.status,
			);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) throw await requestError(response);
	return response.json() as Promise<T>;
}

function sendJson(body: unknown, method: "POST" | "PUT"): RequestInit {
	return {
		method,
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body),
	};
}

export function getRunRetention(): Promise<RunRetentionStatus> {
	return request("/api/maintenance/run-retention/settings");
}

export function updateRunRetention(
	body: RunRetentionSettingsUpdate,
): Promise<RunRetentionStatus> {
	return request(
		"/api/maintenance/run-retention/settings",
		sendJson(body, "PUT"),
	);
}

/** What a window would delete; null previews keeping runs forever. */
export function previewRunRetention(
	days: number | null,
): Promise<RunRetentionPreview> {
	const query = days === null ? "" : `?days=${days}`;
	return request(`/api/maintenance/run-retention/preview${query}`);
}

export function startRunRetention(
	dryRun: boolean,
): Promise<PlatformJobAccepted> {
	return request(
		"/api/maintenance/run-retention/run",
		sendJson({ dry_run: dryRun }, "POST"),
	);
}

export const RUN_RETENTION_DAYS_QUERY_KEY = ["run-retention"] as const;

/**
 * The days finished runs are kept: `null` keeps them forever and `undefined`
 * means the setting has not loaded. It changes only when an admin saves the
 * setting, and that save invalidates the query.
 */
export function useRunRetentionDays(): number | null | undefined {
	const query = useQuery({
		queryKey: RUN_RETENTION_DAYS_QUERY_KEY,
		queryFn: () => request<RunRetentionPublic>("/api/run-retention"),
		staleTime: Infinity,
	});
	return query.data?.days;
}
