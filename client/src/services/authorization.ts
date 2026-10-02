/**
 * The signed-in user's authorization summary (GET /auth/authorization).
 *
 * Keyed by user id so a different sign-in never reads another person's
 * summary; refetched on window focus, after any change to role assignments
 * or role permissions (`invalidateAuthorization` in lib/authorization), and
 * after a refused change (see api-client), so stale controls correct
 * themselves.
 */

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/contexts/AuthContext";
import { apiClient } from "@/lib/api-client";
import {
	AUTHORIZATION_QUERY_KEY,
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";

export async function fetchAuthorization(): Promise<AuthorizationSummary> {
	const { data, error } = await apiClient.GET("/auth/authorization");
	if (error || !data) throw error ?? new Error("Authorization unavailable");
	return data;
}

export function useAuthorization() {
	const { user, isAuthenticated, hasRole } = useAuth();
	const enabled = isAuthenticated && !!user && !hasRole("EmbedUser");
	const query = useQuery({
		queryKey: [...AUTHORIZATION_QUERY_KEY, user?.id ?? null],
		queryFn: fetchAuthorization,
		enabled,
		refetchOnWindowFocus: true,
	});
	const authorization = query.data;
	return {
		authorization,
		isLoading: enabled && query.isLoading,
		isError: query.isError,
		isFetching: query.isFetching,
		refetch: query.refetch,
		isPlatformAdmin: authorization?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authorization, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authorization, permission),
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authorization, requirement),
	};
}
