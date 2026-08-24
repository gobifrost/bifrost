import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type AuthorizationTarget =
	components["schemas"]["AuthorizationTargetPublic"];
export type AuthorizationTargetsResponse =
	components["schemas"]["AuthorizationTargetsPublic"];

export async function getAuthorizationTargets(): Promise<AuthorizationTargetsResponse> {
	const { data, error } = await apiClient.GET("/auth/authorization-targets");

	if (error || !data) {
		throw new Error("Failed to load authorization contexts");
	}

	return data;
}
