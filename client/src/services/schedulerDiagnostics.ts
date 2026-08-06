import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type SchedulerDiagnosticsResponse =
	components["schemas"]["SchedulerDiagnosticsResponse"];

export async function getSchedulerDiagnostics(
	options: { signal?: AbortSignal; logLimit?: number } = {},
): Promise<SchedulerDiagnosticsResponse> {
	const { data, error } = await apiClient.GET("/api/platform/scheduler", {
		params: { query: { log_limit: options.logLimit ?? 100 } },
		signal: options.signal,
	});
	if (error) {
		throw new Error("Failed to load scheduler diagnostics");
	}
	return data;
}
