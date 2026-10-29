import { useQueryClient } from "@tanstack/react-query";

import {
	useReplaceUserRoleAssignments,
	useUserRoleAssignments,
} from "@/hooks/useUsers";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { placeKey, type RolePlace } from "@/lib/role-boundaries";
import { IDENTITIES_QUERY_KEY, type Identity } from "@/services/identities";

export type RecommendedAccess = components["schemas"]["RecommendedAccess"];
export type RecommendedAccessItem =
	components["schemas"]["RecommendedAccessItem"];
export type RecommendedGrant = components["schemas"]["RecommendedGrant"];
export type AssignableRole = components["schemas"]["AssignableRole"];
export type RoleAssignments =
	components["schemas"]["UserRoleAssignmentsResponse"];
type RoleAssignmentsUpdate = components["schemas"]["UserRoleAssignmentsUpdate"];

/** Prefix of every recommended-access query key; changing the identity or its roles invalidates it. */
export const RECOMMENDED_ACCESS_QUERY_KEY = [
	"get",
	"/api/workflows/{workflow_id}/recommended-access",
] as const;

/** Prefix of every run-identities query key. */
export const RUN_IDENTITIES_QUERY_KEY = [
	"get",
	"/api/workflows/{workflow_id}/run-identities",
] as const;

/** The identities a workflow may run as when no person starts it, its default included. */
export function useWorkflowRunIdentities(workflowId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/workflows/{workflow_id}/run-identities",
		{ params: { path: { workflow_id: workflowId! } } },
		{ enabled: !!workflowId },
	);
}

/** What the workflow's runs did that the identity it runs as lacks. */
export function useWorkflowRecommendedAccess(workflowId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/workflows/{workflow_id}/recommended-access",
		{ params: { path: { workflow_id: workflowId! } } },
		{ enabled: !!workflowId },
	);
}

/**
 * Sets `run_identity_id` (null: the organization's default identity). The
 * mutation settles once the queries it changes are refetched, so the
 * recommended access, which names the identity the workflow runs as, is
 * current by then.
 */
export function useSetWorkflowRunIdentity() {
	const queryClient = useQueryClient();
	return $api.useMutation("patch", "/api/workflows/{workflow_id}", {
		onSuccess: () =>
			Promise.all([
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/workflows"],
				}),
				queryClient.invalidateQueries({
					queryKey: RECOMMENDED_ACCESS_QUERY_KEY,
				}),
				queryClient.invalidateQueries({
					queryKey: RUN_IDENTITIES_QUERY_KEY,
				}),
				// Workflows Using moves from one identity to the other.
				queryClient.invalidateQueries({
					queryKey: IDENTITIES_QUERY_KEY,
				}),
			]),
	});
}

/**
 * Whether `identity` is what a workflow of `workflowOrganizationId` runs as
 * when it names none: its organization's default identity, or the global
 * identity for a Global workflow.
 */
export function isDefaultIdentityFor(
	identity: Pick<Identity, "identity_kind" | "organization_id">,
	workflowOrganizationId: string | null,
): boolean {
	return (
		identity.identity_kind !== "custom" &&
		identity.organization_id === workflowOrganizationId
	);
}

/**
 * The identity's role assignments with `grant` added: a role it already
 * holds gains the grant's places, another is appended. The base role stays.
 */
export function mergeGrant(
	current: RoleAssignments,
	grant: RecommendedGrant,
): RoleAssignmentsUpdate {
	const toPlace = (boundary: {
		kind: RolePlace["kind"];
		organization_id?: string | null;
	}): RolePlace => ({
		kind: boundary.kind,
		organization_id: boundary.organization_id ?? null,
	});
	const additional = current.additional.map((role) => ({
		role_id: role.role_id,
		boundaries: role.boundaries.map(toPlace),
	}));
	const held = additional.find((role) => role.role_id === grant.role_id);
	if (held) {
		const keys = new Set(held.boundaries.map(placeKey));
		for (const place of grant.boundaries.map(toPlace))
			if (!keys.has(placeKey(place))) held.boundaries.push(place);
	} else {
		additional.push({
			role_id: grant.role_id,
			boundaries: grant.boundaries.map(toPlace),
		});
	}
	return { base_role_id: current.base_role.id, additional };
}

/**
 * The roles in `roles` (an identity's grantable roles) that can be placed at
 * `organizationId`: additional roles that take an organization boundary,
 * aren't fixed elsewhere, and are allowed at the provider organization when
 * that is where they'd go.
 */
export function rolesGrantableAt(
	roles: AssignableRole[],
	organizationId: string,
	providerOrganizationId: string | undefined,
): AssignableRole[] {
	return roles.filter(
		(role) =>
			role.can_be_additional &&
			role.boundary_kinds.includes("organization") &&
			!role.fixed_boundaries?.length &&
			(role.provider_organization_allowed ||
				organizationId !== providerOrganizationId),
	);
}

/**
 * Grants an identity a recommended role: reads its current assignments,
 * merges the grant in (`mergeGrant`) and replaces them, so nothing it holds
 * is lost. The identity's grantable roles come with it, for a recommendation
 * that leaves the role to choose.
 */
export function useGrantToIdentity(identityId: string | undefined) {
	const queryClient = useQueryClient();
	const assignments = useUserRoleAssignments(identityId);
	const replace = useReplaceUserRoleAssignments();
	return {
		assignableRoles: assignments.data?.assignable_roles ?? [],
		isPending: replace.isPending,
		grant: async (grant: RecommendedGrant) => {
			// A failed refetch keeps the cached roles; granting from them could
			// drop a role given since.
			const current = await assignments.refetch();
			if (current.status !== "success") throw current.error;
			const result = await replace.mutateAsync({
				params: { path: { user_id: identityId! } },
				body: mergeGrant(current.data, grant),
			});
			queryClient.invalidateQueries({
				queryKey: RECOMMENDED_ACCESS_QUERY_KEY,
			});
			queryClient.invalidateQueries({
				queryKey: RUN_IDENTITIES_QUERY_KEY,
			});
			queryClient.invalidateQueries({ queryKey: IDENTITIES_QUERY_KEY });
			return result;
		},
	};
}
