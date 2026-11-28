import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type CloudflareCapacityStatus =
	components["schemas"]["CloudflareCapacityStatus"];
export type CloudflareCapacityConnectRequest =
	components["schemas"]["CloudflareCapacityConnectRequest"];
export type CloudflareCapacityConnectAccepted =
	components["schemas"]["CloudflareCapacityConnectAccepted"];
export type CloudflareCapacityRules =
	components["schemas"]["CloudflareCapacityRules"];

function responseError(error: unknown, fallback: string): Error {
	if (typeof error === "object" && error !== null) {
		const body = error as { detail?: unknown; message?: unknown };
		if (typeof body.detail === "string") return new Error(body.detail);
		if (typeof body.message === "string") return new Error(body.message);
	}
	return new Error(fallback);
}

export async function getCloudflareCapacity(): Promise<CloudflareCapacityStatus> {
	const { data, error } = await apiClient.GET(
		"/api/admin/worker-capacity/cloudflare",
	);
	if (error) throw responseError(error, "Could not load Cloudflare capacity.");
	if (!data) throw new Error("Cloudflare capacity did not return a status.");
	return data;
}

export async function connectCloudflareCapacity(
	request: CloudflareCapacityConnectRequest,
): Promise<CloudflareCapacityConnectAccepted> {
	const { data, error } = await apiClient.POST(
		"/api/admin/worker-capacity/cloudflare",
		{ body: request },
	);
	if (error) throw responseError(error, "Could not start Cloudflare setup.");
	if (!data) throw new Error("Cloudflare setup did not return a job.");
	return data;
}

export async function updateCloudflareCapacityRules(
	rules: CloudflareCapacityRules,
): Promise<CloudflareCapacityStatus> {
	const { data, error } = await apiClient.PUT(
		"/api/admin/worker-capacity/cloudflare/rules",
		{ body: rules },
	);
	if (error) throw responseError(error, "Could not save Cloudflare start rules.");
	if (!data) throw new Error("Cloudflare rules did not return a status.");
	return data;
}
