import { useQuery } from "@tanstack/react-query";

import { $api, authFetch } from "@/lib/api-client";
import { ApiError, parseApiError } from "@/lib/api-error";
import type { components } from "@/lib/v1";

export type PermissionCatalogEntry =
	components["schemas"]["PermissionCatalogEntry"];

export type PlaceKind =
	"home" | "organization" | "managed_organizations" | "platform";

/** A place where access applies: one organization, all customers, or Global. */
export interface Place {
	kind: PlaceKind;
	organization_id: string | null;
	organization_name: string | null;
	label: string;
}

/** The role that gives a permission, and whether it is base or additional. */
export interface AccessGrantSource {
	role_id: string;
	role_name: string;
	via: "base" | "additional";
}

export interface AccessGrant {
	permission: string;
	domain: string;
	action: string;
	scope: Exclude<PermissionCatalogEntry["scope"], "varies">;
	sources: AccessGrantSource[];
}

export interface AccessRow {
	place: Place;
	grants: AccessGrant[];
}

/** What a person can do, and where: `GET /api/users/{user_id}/access`. */
export interface UserAccessMap {
	user_id: string;
	name: string;
	email: string;
	home_organization: { id: string; name: string } | null;
	is_platform_admin: boolean;
	is_protected: boolean;
	privileged_permissions: string[];
	reach: Place[];
	rows: AccessRow[];
}

/** Prefix of every access-map query key; mutations that change access invalidate it. */
export const USER_ACCESS_QUERY_KEY = [
	"get",
	"/api/users/{user_id}/access",
] as const;

/** Every permission domain: its title, area, guidance, actions, scope and enforcement. */
export function usePermissionCatalog() {
	return $api.useQuery("get", "/api/permissions/catalog");
}

async function getUserAccessMap(userId: string): Promise<UserAccessMap> {
	const response = await authFetch(`/api/users/${userId}/access`);
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw body && typeof body === "object" && "detail" in body
			? parseApiError(body, response.status)
			: new ApiError(
					`Access request failed: ${response.statusText}`,
					response.status,
				);
	}
	return response.json() as Promise<UserAccessMap>;
}

/** What a person can do, and where it applies. */
export function useUserAccessMap(userId: string | undefined) {
	return useQuery({
		queryKey: [
			...USER_ACCESS_QUERY_KEY,
			{ params: { path: { user_id: userId } } },
		],
		queryFn: () => getUserAccessMap(userId!),
		enabled: !!userId,
	});
}
