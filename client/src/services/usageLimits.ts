import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type UsageLimitScope = "platform" | "organization" | "user" | "solution";
export type UsageLimitPeriod = "daily" | "monthly";
export type UsageLimitCeilings = components["schemas"]["UsageLimitCeilings"];
export type UsageLimitPolicy =
	components["schemas"]["UsageLimitPolicyPublic"];
export type UsageLimitPolicyUpsert =
	components["schemas"]["UsageLimitPolicyUpsert"];
export type UsageLimitListResponse =
	components["schemas"]["UsageLimitListResponse"];
export type UsageLimitEffectiveResponse =
	components["schemas"]["UsageLimitEffectiveResponse"];

export interface UsageLimitTarget {
	scope: UsageLimitScope;
	targetId: string;
}

export interface UsageLimitRequestOptions {
	boundary?: string;
}

function headersFor(options?: UsageLimitRequestOptions) {
	return options?.boundary
		? { "X-Bifrost-Boundary": options.boundary }
		: undefined;
}

function message(error: unknown, fallback: string): string {
	if (!error) return fallback;
	if (typeof error === "string") return error;
	if (typeof error === "object" && "detail" in error) {
		const detail = (error as { detail?: unknown }).detail;
		if (typeof detail === "string") return detail;
	}
	return `${fallback}: ${JSON.stringify(error)}`;
}

export async function listUsageLimits(
	options: UsageLimitRequestOptions = {},
): Promise<UsageLimitListResponse> {
	const { data, error } = await apiClient.GET(
		"/api/settings/ai/usage-limits",
		{ headers: headersFor(options) },
	);
	if (error) throw new Error(message(error, "Failed to load usage limits"));
	return data ?? { policies: [] };
}

export async function getEffectiveUsageLimits(
	target: UsageLimitTarget,
	options: UsageLimitRequestOptions = {},
): Promise<UsageLimitEffectiveResponse> {
	const { data, error } = await apiClient.GET(
		"/api/settings/ai/usage-limits/effective/{scope}/{target_id}",
		{
			headers: headersFor(options),
			params: {
				path: { scope: target.scope, target_id: target.targetId },
			},
		},
	);
	if (error) throw new Error(message(error, "Failed to load effective usage limits"));
	return data as UsageLimitEffectiveResponse;
}

export async function saveUsageLimit(
	target: UsageLimitTarget,
	payload: UsageLimitPolicyUpsert,
	options: UsageLimitRequestOptions = {},
): Promise<UsageLimitPolicy> {
	const { data, error } = await apiClient.PUT(
		"/api/settings/ai/usage-limits/{scope}/{target_id}",
		{
			headers: headersFor(options),
			params: {
				path: { scope: target.scope, target_id: target.targetId },
			},
			body: payload,
		},
	);
	if (error) throw new Error(message(error, "Failed to save usage limit"));
	return data as UsageLimitPolicy;
}

export async function deleteUsageLimit(
	target: UsageLimitTarget,
	options: UsageLimitRequestOptions = {},
): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/settings/ai/usage-limits/{scope}/{target_id}",
		{
			headers: headersFor(options),
			params: {
				path: { scope: target.scope, target_id: target.targetId },
			},
		},
	);
	if (error) throw new Error(message(error, "Failed to delete usage limit"));
}
