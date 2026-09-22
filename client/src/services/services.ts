/**
 * Supervised services API client.
 *
 * Services are long-lived `@service` executables managed through desired
 * state (start/stop/restart/enable/disable) rather than one-shot execution.
 *
 * `ServiceResponse` embeds the live-attempt summary (`active_attempt`),
 * the newest terminal exit (`last_exit_reason`), and worker-reported
 * memory (`memory_mb`, <90s old) — one round trip, no attempts fan-out.
 */

import { apiClient } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

// Auto-generated types from OpenAPI spec
export type Service = components["schemas"]["ServiceResponse"];
export type ServiceList = components["schemas"]["ServiceListResponse"];
export type ServiceAttempt =
	components["schemas"]["ServiceAttemptResponse"];
export type ServiceAttemptList =
	components["schemas"]["ServiceAttemptListResponse"];
export type ServicePolicyUpdate =
	components["schemas"]["ServicePolicyUpdate"];
export type ServiceLog = components["schemas"]["ServiceLogResponse"];
export type ServiceLogList = components["schemas"]["ServiceLogListResponse"];

/**
 * UI state contract for one Services-list row. The contract fields
 * (`active_attempt`, `last_exit_reason`, `memory_mb`) arrive embedded in
 * `ServiceResponse` — no separate attempt fetch per row.
 */
export type ServiceListItem = Service;

export type ServiceAction =
	| "start"
	| "stop"
	| "restart"
	| "enable"
	| "disable";

/** Live-query keys for services (shared by queries + action invalidation). */
export const SERVICES_QUERY_KEY = ["services"] as const;
export function serviceDetailQueryKey(serviceId: string): string[] {
	return ["service", serviceId];
}

function actionError(action: ServiceAction, error: unknown): Error {
	return new Error(
		getErrorMessage(error, `Could not ${action} the service.`),
	);
}

async function postServiceAction(
	serviceId: string,
	action: ServiceAction,
	path:
		| "/api/services/{service_id}/start"
		| "/api/services/{service_id}/stop"
		| "/api/services/{service_id}/restart"
		| "/api/services/{service_id}/enable"
		| "/api/services/{service_id}/disable",
): Promise<Service> {
	const { data, error } = await apiClient.POST(path, {
		params: { path: { service_id: serviceId } },
	});
	if (error || !data) throw actionError(action, error);
	return data;
}

export async function listServices(
	limit = 100,
	offset = 0,
): Promise<ServiceList> {
	const { data, error } = await apiClient.GET("/api/services", {
		params: { query: { limit, offset } },
	});
	if (error || !data)
		throw new Error(
			getErrorMessage(error, "Could not load services."),
		);
	return data;
}

export async function getService(serviceId: string): Promise<Service> {
	const { data, error } = await apiClient.GET(
		"/api/services/{service_id}",
		{
			params: { path: { service_id: serviceId } },
		},
	);
	if (error || !data)
		throw new Error(
			getErrorMessage(error, "Could not load the service."),
		);
	return data;
}

export async function updateServicePolicy(
	serviceId: string,
	policy: ServicePolicyUpdate,
): Promise<Service> {
	const { data, error } = await apiClient.PATCH(
		"/api/services/{service_id}",
		{
			params: { path: { service_id: serviceId } },
			body: policy,
		},
	);
	if (error || !data)
		throw new Error(
			getErrorMessage(error, "Could not update the service policy."),
		);
	return data;
}

export async function startService(serviceId: string): Promise<Service> {
	return postServiceAction(
		serviceId,
		"start",
		"/api/services/{service_id}/start",
	);
}

export async function stopService(serviceId: string): Promise<Service> {
	return postServiceAction(
		serviceId,
		"stop",
		"/api/services/{service_id}/stop",
	);
}

export async function restartService(serviceId: string): Promise<Service> {
	return postServiceAction(
		serviceId,
		"restart",
		"/api/services/{service_id}/restart",
	);
}

export async function enableService(serviceId: string): Promise<Service> {
	return postServiceAction(
		serviceId,
		"enable",
		"/api/services/{service_id}/enable",
	);
}

export async function disableService(serviceId: string): Promise<Service> {
	return postServiceAction(
		serviceId,
		"disable",
		"/api/services/{service_id}/disable",
	);
}

export async function listServiceAttempts(
	serviceId: string,
	limit = 100,
	offset = 0,
): Promise<ServiceAttemptList> {
	const { data, error } = await apiClient.GET(
		"/api/services/{service_id}/attempts",
		{
			params: { path: { service_id: serviceId }, query: { limit, offset } },
		},
	);
	if (error || !data)
		throw new Error(
			getErrorMessage(error, "Could not load service attempts."),
		);
	return data;
}

export interface ServiceLogFilters {
	attemptId?: string;
	levels?: string[];
	startDate?: string;
	endDate?: string;
	limit?: number;
	continuationToken?: string;
	order?: "chronological" | "newest_first";
}

export async function listServiceLogs(
	serviceId: string,
	filters: ServiceLogFilters = {},
): Promise<ServiceLogList> {
	const { data, error } = await apiClient.GET(
		"/api/services/{service_id}/logs",
		{
			params: {
				path: { service_id: serviceId },
				query: {
					attempt_id: filters.attemptId,
					levels: filters.levels,
					start_date: filters.startDate,
					end_date: filters.endDate,
					limit: filters.limit ?? 200,
					continuation_token: filters.continuationToken,
					order: filters.order ?? "chronological",
				},
			},
		},
	);
	if (error || !data)
		throw new Error(
			getErrorMessage(error, "Could not load service logs."),
		);
	return data;
}
