import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type PermissionCatalogEntry =
	components["schemas"]["PermissionCatalogEntry"];

export type Place = components["schemas"]["Place"];
export type PlaceKind = Place["kind"];
export type AccessGrantSource = components["schemas"]["AccessGrantSource"];
export type AccessGrant = components["schemas"]["AccessGrant"];
export type AccessRow = components["schemas"]["AccessRow"];
export type UserAccessMap = components["schemas"]["UserAccessMap"];

/** Prefix of every access-map query key; mutations that change access invalidate it. */
export const USER_ACCESS_QUERY_KEY = [
	"get",
	"/api/users/{user_id}/access",
] as const;

/** Every permission domain: its title, area, guidance, actions, scope and enforcement. */
export function usePermissionCatalog(enabled = true) {
	return $api.useQuery("get", "/api/permissions/catalog", undefined, {
		enabled,
	});
}

/** What a person can do, and where it applies. */
export function useUserAccessMap(userId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/users/{user_id}/access",
		{ params: { path: { user_id: userId! } } },
		{ enabled: !!userId },
	);
}
