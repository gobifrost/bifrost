/**
 * React Query hooks for user management
 * Uses openapi-react-query for type-safe API calls
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";

/**
 * Fetch all users filtered by current scope.
 */
export function useUsers() {
	return $api.useQuery("get", "/api/users", {});
}

/**
 * Fetch users with optional scope filter.
 *
 * @param scope - Organization scope filter:
 *   - undefined: all users (platform admins only)
 *   - null: global/platform users only (no org assignment)
 *   - UUID string: users in that specific org
 */
export function useUsersFiltered(
	scope?: string | null,
	includeInactive?: boolean,
) {
	// Build query params - convert null to "global" for the API
	const queryParams: { scope?: string; include_inactive?: boolean } = {};
	if (scope === null) {
		queryParams.scope = "global";
	} else if (scope !== undefined) {
		queryParams.scope = scope;
	}
	if (includeInactive) {
		queryParams.include_inactive = true;
	}

	return $api.useQuery("get", "/api/users", {
		params: {
			query: queryParams,
		},
	});
}

/**
 * Fetch a specific user by ID
 */
export function useUser(userId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/users/{user_id}",
		{ params: { path: { user_id: userId! } } },
		{ enabled: !!userId },
	);
}

/**
 * Fetch roles for a specific user
 */
export function useUserRoles(userId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/users/{user_id}/roles",
		{ params: { path: { user_id: userId! } } },
		{ enabled: !!userId },
	);
}

/**
 * Fetch forms accessible to a specific user
 */
export function useUserForms(userId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/users/{user_id}/forms",
		{ params: { path: { user_id: userId! } } },
		{ enabled: !!userId },
	);
}

/**
 * Create a new user
 */
export function useCreateUser() {
	const queryClient = useQueryClient();
	return $api.useMutation("post", "/api/users", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
		},
	});
}

/**
 * Update an existing user
 */
export function useUpdateUser() {
	const queryClient = useQueryClient();
	return $api.useMutation("patch", "/api/users/{user_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
		},
	});
}

/**
 * Delete a user
 */
export function useDeleteUser() {
	const queryClient = useQueryClient();
	return $api.useMutation("delete", "/api/users/{user_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
		},
	});
}
