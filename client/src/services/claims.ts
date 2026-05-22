import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type CustomClaim = components["schemas"]["CustomClaim"];
export type CustomClaimCreate = components["schemas"]["CustomClaimCreate"];
export type CustomClaimUpdate = components["schemas"]["CustomClaimUpdate"];
export type ClaimsList = components["schemas"]["ClaimsList"];

interface RequestOptions {
	signal?: AbortSignal;
}

function errorMessage(error: unknown, fallback: string): string {
	if (
		error &&
		typeof error === "object" &&
		"detail" in error &&
		typeof error.detail === "string"
	) {
		return error.detail;
	}
	return fallback;
}

export async function listClaims(
	options: RequestOptions = {},
): Promise<ClaimsList> {
	const { data, error } = await apiClient.GET("/api/claims", {
		...options,
	});
	if (error) throw new Error(errorMessage(error, "Failed to list claims"));
	return data;
}

export async function getClaim(
	name: string,
	options: RequestOptions = {},
): Promise<CustomClaim> {
	const { data, error } = await apiClient.GET("/api/claims/{name}", {
		params: { path: { name } },
		...options,
	});
	if (error) throw new Error(errorMessage(error, "Failed to get claim"));
	return data;
}

export async function createClaim(
	body: CustomClaimCreate,
	options: RequestOptions = {},
): Promise<CustomClaim> {
	const { data, error } = await apiClient.POST("/api/claims", {
		body,
		...options,
	});
	if (error) throw new Error(errorMessage(error, "Failed to create claim"));
	return data;
}

export async function updateClaim(
	name: string,
	body: CustomClaimUpdate,
	options: RequestOptions = {},
): Promise<CustomClaim> {
	const { data, error } = await apiClient.PATCH("/api/claims/{name}", {
		params: { path: { name } },
		body,
		...options,
	});
	if (error) throw new Error(errorMessage(error, "Failed to update claim"));
	return data;
}

export async function deleteClaim(
	name: string,
	options: RequestOptions = {},
): Promise<void> {
	const { error } = await apiClient.DELETE("/api/claims/{name}", {
		params: { path: { name } },
		...options,
	});
	if (error) throw new Error(errorMessage(error, "Failed to delete claim"));
}
