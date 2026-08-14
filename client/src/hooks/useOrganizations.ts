/**
 * React Query hooks for organization management
 * Uses openapi-react-query for type-safe API calls
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import { toast } from "sonner";

/**
 * Fetch active organizations visible to the current user, optionally including inactive ones
 */
export function useOrganizations(options?: {
	enabled?: boolean;
	includeInactive?: boolean;
}) {
	return $api.useQuery(
		"get",
		"/api/organizations",
		{
			params: {
				query: {
					include_inactive: options?.includeInactive ?? false,
				},
			},
		},
		{ enabled: options?.enabled ?? true },
	);
}

/**
 * Fetch a specific organization by ID
 */
export function useOrganization(orgId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/organizations/{org_id}",
		{
			params: { path: { org_id: orgId! } },
		},
		{
			enabled: !!orgId,
		},
	);
}

/**
 * Create a new organization
 */
export function useCreateOrganization() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/organizations", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/organizations"],
			});
			toast.success("Organization created", {
				description: "The organization has been successfully created",
			});
		},
		onError: (error) => {
			toast.error("Failed to create organization", {
				description: getErrorMessage(error, "Unknown error occurred"),
			});
		},
	});
}

/**
 * Update an existing organization
 */
export function useUpdateOrganization() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/organizations/{org_id}", {
		onSuccess: (_data, variables) => {
			const orgId = variables.params?.path?.org_id;
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/organizations"],
			});
			if (orgId) {
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/organizations/{org_id}",
						{ params: { path: { org_id: orgId } } },
					],
				});
			}
			toast.success("Organization updated", {
				description: "The organization has been successfully updated",
			});
		},
		onError: (error) => {
			toast.error("Failed to update organization", {
				description: getErrorMessage(error, "Unknown error occurred"),
			});
		},
	});
}
