/**
 * React Query hooks for roles management using openapi-react-query pattern
 */

import {
	keepPreviousData,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { invalidateAuthorization } from "@/lib/authorization";
import { toast } from "sonner";
type RoleCreate = components["schemas"]["RoleCreate"];
type AssignUsersToRoleRequest =
	components["schemas"]["AssignUsersToRoleRequest"];
type AssignFormsToRoleRequest =
	components["schemas"]["AssignFormsToRoleRequest"];

export function useRoles(options: { enabled?: boolean } = {}) {
	return $api.useQuery("get", "/api/roles", {}, options);
}

export interface RolesPageParams {
	search?: string;
	sortBy: "name" | "created";
	sortDirection: "asc" | "desc";
	limit: number;
	offset: number;
}

export function useRolesPage(params: RolesPageParams) {
	return useQuery({
		queryKey: ["get", "/api/roles", { page: params }],
		queryFn: async () => {
			const { data, error, response } = await apiClient.GET(
				"/api/roles",
				{
					params: {
						query: {
							search: params.search?.trim() || undefined,
							sort_by: params.sortBy,
							sort_direction: params.sortDirection,
							limit: params.limit,
							offset: params.offset,
						},
					},
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

/** A role by id, builtin roles included (they show read-only). */
export function useRole(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}",
		{
			params: {
				path: { role_id: roleId ?? "" },
				query: { include_builtin: true },
			},
		},
		{ enabled: !!roleId },
	);
}

/** Every permission a role holds, and the identity permissions an editor
 * may choose from. */
export function useRolePermissions(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}/permissions",
		{ params: { path: { role_id: roleId ?? "" } } },
		{ enabled: !!roleId },
	);
}

/** Replace a role's identity permissions; its other permissions are kept. */
export function useUpdateRolePermissions() {
	const queryClient = useQueryClient();
	return $api.useMutation("put", "/api/roles/{role_id}/permissions", {
		onSuccess: (data, variables) => {
			queryClient.setQueryData(
				[
					"get",
					"/api/roles/{role_id}/permissions",
					{
						params: {
							path: { role_id: variables.params.path.role_id },
						},
					},
				],
				data,
			);
			// Holders may have become (or stopped being) protected users.
			queryClient.invalidateQueries({ queryKey: ["get", "/api/users"] });
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}/role-assignments"],
			});
			void invalidateAuthorization(queryClient);
		},
	});
}

export function useCreateRole() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/roles", {
		onSuccess: (_, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/roles"] });
			const name = (variables.body as RoleCreate)?.name || "Role";
			toast.success("Role created", {
				description: `Role "${name}" has been created`,
			});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to create role";
			toast.error("Failed to create role", {
				description: message,
			});
		},
	});
}

export function useUpdateRole() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/roles/{role_id}", {
		onSuccess: async () => {
			await Promise.all([
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/roles"],
				}),
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/roles/{role_id}"],
				}),
			]);
			toast.success("Role updated", {
				description: "The role has been updated successfully",
			});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to update role";
			toast.error("Failed to update role", {
				description: message,
			});
		},
	});
}

export function useDeleteRole() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/roles/{role_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/roles"] });
			toast.success("Role deleted", {
				description: "The role has been removed",
			});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to delete role";
			toast.error("Failed to delete role", {
				description: message,
			});
		},
	});
}

export interface RoleUsersPageParams {
	search?: string;
	limit: number;
	offset: number;
}

export function useRoleUsersPage(
	roleId: string | undefined,
	params: RoleUsersPageParams,
) {
	return useQuery({
		queryKey: [
			"get",
			"/api/roles/{role_id}/users",
			{ roleId, page: params },
		],
		enabled: !!roleId,
		queryFn: async () => {
			const { data, error } = await apiClient.GET(
				"/api/roles/{role_id}/users",
				{
					params: {
						path: { role_id: roleId ?? "" },
						query: {
							search: params.search?.trim() || undefined,
							limit: params.limit,
							offset: params.offset,
						},
					},
				},
			);
			if (error) throw error;
			return data;
		},
		placeholderData: keepPreviousData,
	});
}

export function useAssignUsersToRole(options: { toast?: boolean } = {}) {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/roles/{role_id}/users", {
		onSuccess: (_, variables) => {
			const userIds =
				(variables.body as AssignUsersToRoleRequest)?.user_ids || [];
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/users"],
			});
			invalidateRoleList(queryClient);
			if (options.toast !== false)
				toast.success("Users assigned", {
					description: `${userIds.length} user(s) assigned to role`,
				});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to assign users";
			if (options.toast !== false)
				toast.error("Failed to assign users", {
					description: message,
				});
		},
	});
}

export function useRemoveUserFromRole(options: { toast?: boolean } = {}) {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/roles/{role_id}/users/{user_id}", {
		onSuccess: (_, variables) => {
			const roleId = variables.params?.path?.role_id;
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/roles/{role_id}/users",
					{ params: { path: { role_id: roleId } } },
				],
			});
			if (options.toast !== false)
				toast.success("User removed", {
					description: "User has been removed from the role",
				});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to remove user";
			if (options.toast !== false)
				toast.error("Failed to remove user", {
					description: message,
				});
		},
	});
}

export function useRoleForms(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}/forms",
		{ params: { path: { role_id: roleId ?? "" } } },
		{ enabled: !!roleId },
	);
}

export function useAssignFormsToRole() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/roles/{role_id}/forms", {
		onSuccess: (_, variables) => {
			const roleId = variables.params?.path?.role_id;
			const formIds =
				(variables.body as AssignFormsToRoleRequest)?.form_ids || [];
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/roles/{role_id}/forms",
					{ params: { path: { role_id: roleId } } },
				],
			});
			toast.success("Forms assigned", {
				description: `${formIds.length} form(s) assigned to role`,
			});
		},
		onError: (error) => {
			const message =
				typeof error === "object" && error && "detail" in error
					? String(error.detail)
					: "Failed to assign forms";
			toast.error("Failed to assign forms", {
				description: message,
			});
		},
	});
}

/**
 * Assign roles to a form - imperative function using apiClient
 * This handles the form->roles relationship by assigning each role to the form
 */
export async function assignRolesToForm(
	formId: string,
	roleIds: string[],
): Promise<void> {
	// Assign each role to this form
	for (const roleId of roleIds) {
		const { error } = await apiClient.POST("/api/roles/{role_id}/forms", {
			params: { path: { role_id: roleId } },
			body: { form_ids: [formId] },
		});
		if (error) throw new Error(`Failed to assign role to form: ${error}`);
	}
}

// =============================================================================
// Agents / Apps / Workflows — consumer-tab hooks (Block 5)
// =============================================================================

function invalidateRoleList(qc: ReturnType<typeof useQueryClient>) {
	qc.invalidateQueries({ queryKey: ["get", "/api/roles"] });
	qc.invalidateQueries({ queryKey: ["get", "/api/roles/{role_id}"] });
}

export function useRoleAgents(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}/agents",
		{ params: { path: { role_id: roleId ?? "" } } },
		{ enabled: !!roleId },
	);
}

export function useAssignAgentsToRole() {
	const qc = useQueryClient();
	return $api.useMutation("post", "/api/roles/{role_id}/agents", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/agents"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useBulkUnassignAgents() {
	const qc = useQueryClient();
	return $api.useMutation("delete", "/api/roles/{role_id}/agents", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/agents"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useBulkUnassignUsers() {
	const qc = useQueryClient();
	return $api.useMutation("delete", "/api/roles/{role_id}/users", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/users"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useBulkUnassignForms() {
	const qc = useQueryClient();
	return $api.useMutation("delete", "/api/roles/{role_id}/forms", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/forms"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useRoleApps(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}/apps",
		{ params: { path: { role_id: roleId ?? "" } } },
		{ enabled: !!roleId },
	);
}

export function useAssignAppsToRole() {
	const qc = useQueryClient();
	return $api.useMutation("post", "/api/roles/{role_id}/apps", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/apps"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useBulkUnassignApps() {
	const qc = useQueryClient();
	return $api.useMutation("delete", "/api/roles/{role_id}/apps", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/apps"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useRoleWorkflows(roleId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/roles/{role_id}/workflows",
		{ params: { path: { role_id: roleId ?? "" } } },
		{ enabled: !!roleId },
	);
}

export function useAssignWorkflowsToRole() {
	const qc = useQueryClient();
	return $api.useMutation("post", "/api/roles/{role_id}/workflows", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/workflows"],
			});
			invalidateRoleList(qc);
		},
	});
}

export function useBulkUnassignWorkflows() {
	const qc = useQueryClient();
	return $api.useMutation("delete", "/api/roles/{role_id}/workflows", {
		onSuccess: () => {
			qc.invalidateQueries({
				queryKey: ["get", "/api/roles/{role_id}/workflows"],
			});
			invalidateRoleList(qc);
		},
	});
}
