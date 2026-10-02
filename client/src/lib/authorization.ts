/**
 * What the signed-in user may do, for deciding which controls to show.
 *
 * Mirrors the server's coverage rule (api/src/services/authorization/
 * evaluator.py `_covers`) over the summary from GET /auth/authorization.
 * The server decides every request on its own; these helpers only keep the
 * UI from offering what it would refuse.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { components } from "@/lib/v1";

export type AuthorizationSummary =
	components["schemas"]["AuthorizationSummary"];
export type AuthorizationGrant = components["schemas"]["AuthorizationGrant"];

/** Where an action happens: in one organization, or Global / platform-wide
 * (Global users, role definitions, creating organizations). */
export type AuthorizationTarget =
	{ kind: "org"; id: string } | { kind: "global" };

/** A route or navigation item's requirement: the permission held anywhere,
 * or held platform-wide. */
export interface PermissionRequirement {
	permission: string;
	at?: "anywhere" | "global";
}

export const AUTHORIZATION_QUERY_KEY = ["authorization"] as const;

/** Refetch the caller's authorization, after a change to role assignments
 * or role permissions. */
export function invalidateAuthorization(queryClient: QueryClient) {
	return queryClient.invalidateQueries({ queryKey: AUTHORIZATION_QUERY_KEY });
}

/** The one permission the Platform Admin wildcard leaves out. */
const WILDCARD_EXCLUDED_PERMISSIONS = new Set(["secrets.read"]);

export const GLOBAL_TARGET: AuthorizationTarget = { kind: "global" };

/** The target for a row that belongs to `organizationId` (null: Global). */
export function orgTarget(
	organizationId: string | null | undefined,
): AuthorizationTarget {
	return organizationId ? { kind: "org", id: organizationId } : GLOBAL_TARGET;
}

function isAdminFor(authz: AuthorizationSummary, permission: string): boolean {
	return (
		authz.is_platform_admin &&
		!WILDCARD_EXCLUDED_PERMISSIONS.has(permission)
	);
}

function covers(
	authz: AuthorizationSummary,
	grant: AuthorizationGrant,
	target: AuthorizationTarget,
): boolean {
	const boundary = grant.boundary;
	if (target.kind === "global") return boundary.kind === "platform";
	switch (boundary.kind) {
		case "home":
			return (
				authz.home_organization_id !== null &&
				target.id === authz.home_organization_id
			);
		case "organization":
			return boundary.organization_id === target.id;
		case "managed_organizations":
			return target.id !== authz.provider_organization_id;
		case "platform":
			return false;
	}
}

/** Whether `permission` is held at `target`. */
export function canAt(
	authz: AuthorizationSummary | undefined,
	permission: string,
	target: AuthorizationTarget,
): boolean {
	if (!authz) return false;
	if (isAdminFor(authz, permission)) return true;
	return authz.grants.some(
		(grant) =>
			grant.permission === permission && covers(authz, grant, target),
	);
}

/** Whether `permission` is held at any boundary at all. */
export function canAnywhere(
	authz: AuthorizationSummary | undefined,
	permission: string,
): boolean {
	if (!authz) return false;
	if (isAdminFor(authz, permission)) return true;
	return authz.grants.some((grant) => grant.permission === permission);
}

export function meetsRequirement(
	authz: AuthorizationSummary | undefined,
	requirement: PermissionRequirement,
): boolean {
	return requirement.at === "global"
		? canAt(authz, requirement.permission, GLOBAL_TARGET)
		: canAnywhere(authz, requirement.permission);
}
