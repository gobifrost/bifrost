/**
 * React Query hooks for user management
 * Uses openapi-react-query for type-safe API calls
 */

import {
	keepPreviousData,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import { invalidateAuthorization } from "@/lib/authorization";

export interface UsersPageParams {
	scope?: string | null;
	includeInactive?: boolean;
	search?: string;
	sortBy: "name" | "email" | "status" | "created" | "last_login";
	sortDirection: "asc" | "desc";
	limit: number;
	offset: number;
}

export function useUsersPage(params: UsersPageParams) {
	return useQuery({
		queryKey: ["get", "/api/users", { page: params }],
		queryFn: async () => {
			const query = {
				scope: params.scope === null ? "global" : params.scope,
				include_inactive: params.includeInactive || undefined,
				search: params.search?.trim() || undefined,
				sort_by: params.sortBy,
				sort_direction: params.sortDirection,
				limit: params.limit,
				offset: params.offset,
			};
			const { data, error, response } = await apiClient.GET(
				"/api/users",
				{
					params: { query },
				},
			);
			if (error) throw error;
			return {
				items: data ?? [],
				total: Number(
					response.headers.get("X-Total-Count") ?? data?.length ?? 0,
				),
			};
		},
		placeholderData: keepPreviousData,
	});
}

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
	enabled = true,
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

	return $api.useQuery(
		"get",
		"/api/users",
		{
			params: {
				query: queryParams,
			},
		},
		{ enabled },
	);
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
 * A user's base role, additional roles (with where each applies), and the
 * roles the caller may grant them.
 */
export function useUserRoleAssignments(
	userId: string | undefined,
	enabled = true,
) {
	return $api.useQuery(
		"get",
		"/api/users/{user_id}/role-assignments",
		{ params: { path: { user_id: userId! } } },
		{ enabled: !!userId && enabled },
	);
}

/**
 * Replace a user's base role and additional roles in one request.
 */
export function useReplaceUserRoleAssignments() {
	const queryClient = useQueryClient();
	return $api.useMutation("put", "/api/users/{user_id}/role-assignments", {
		onSuccess: (data, variables) => {
			queryClient.setQueryData(
				[
					"get",
					"/api/users/{user_id}/role-assignments",
					{
						params: {
							path: { user_id: variables.params.path.user_id },
						},
					},
				],
				data,
			);
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}"],
			});
			queryClient.invalidateQueries({ queryKey: ["get", "/api/roles"] });
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/users"],
			});
			void invalidateAuthorization(queryClient);
		},
	});
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
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}"],
			});
			// A move changes which roles the user can hold and where.
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}/role-assignments"],
			});
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

/**
 * Reset a user's MFA: removes their authenticator app, recovery codes,
 * passkeys and remembered devices, and signs them out everywhere.
 */
export function useResetUserMfa() {
	return $api.useMutation("post", "/api/users/{user_id}/mfa/reset");
}

/**
 * Sign a user out of every device.
 */
export function useSignOutUserEverywhere() {
	return $api.useMutation("post", "/auth/admin/revoke-user");
}

/**
 * Bulk user operation — move_org / replace_roles / set_active.
 *
 * Returns BulkUserResponse with succeeded[] and failed[{user_id, reason}].
 * Always invalidates the users list on success so the table reflects new
 * org/role/active state.
 */
export function useBulkUserOperation() {
	const queryClient = useQueryClient();
	return $api.useMutation("patch", "/api/users/bulk", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
		},
	});
}
