/**
 * React Query hooks for config management
 * Uses openapi-react-query pattern with $api for type-safe queries and mutations
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import { toast } from "sonner";

type SetConfigRequest = components["schemas"]["SetConfigRequest"];
type Config = components["schemas"]["ConfigResponse"];

/**
 * Hook to fetch all configs with optional organization filtering
 * @param filterScope - Filter scope: undefined = all, null = global only, string = org UUID
 *
 * The scope query param controls filtering:
 * - Omitted (undefined): show all configs (superusers) / user's org + global (org users)
 * - "global": show only global configs (org_id IS NULL)
 * - UUID string: show that org's configs + global configs
 */
export function useConfigs(filterScope?: string | null) {
	// Build query params - scope is the new filter parameter
	const queryParams: Record<string, string | undefined> = {};
	if (filterScope === null) {
		// null means "global only"
		queryParams.scope = "global";
	} else if (filterScope !== undefined) {
		// UUID string means specific org
		queryParams.scope = filterScope;
	}
	// undefined = don't send scope (show all)

	return $api.useQuery("get", "/api/config", {
		params: {
			// Type assertion needed until types are regenerated
			query:
				Object.keys(queryParams).length > 0 ? queryParams : undefined,
		} as { query?: { scope?: string } },
	});
}

export function useSetConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/config", {
		onSuccess: (_, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/config"] });
			toast.success("Configuration saved", {
				description: `Config key "${variables.body.key}" has been updated`,
			});
		},
		onError: (error) => {
			const errorMessage =
				typeof error === "object" && error && "detail" in error
					? String((error as Record<string, unknown>)["detail"])
					: "Unknown error";
			toast.error("Failed to save configuration", {
				description: errorMessage,
			});
		},
	});
}

export function useUpdateConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/config/{config_id}", {
		onSuccess: (_, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/config"] });
			toast.success("Configuration saved", {
				description: `Config key "${variables.body.key}" has been updated`,
			});
		},
		onError: (error) => {
			const errorMessage =
				typeof error === "object" && error && "detail" in error
					? String((error as Record<string, unknown>)["detail"])
					: "Unknown error";
			toast.error("Failed to save configuration", {
				description: errorMessage,
			});
		},
	});
}

export function useDeleteConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/config/{config_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/config"] });
			toast.success("Configuration deleted");
		},
		onError: (error) => {
			const errorMessage =
				typeof error === "object" && error && "detail" in error
					? String((error as Record<string, unknown>)["detail"])
					: "Unknown error";
			toast.error("Failed to delete configuration", {
				description: errorMessage,
			});
		},
	});
}

/**
 * Standalone function for imperative config operations outside React hooks
 * Use when you need to set config without React Query machinery
 */
export async function setConfigImperative(
	request: SetConfigRequest,
): Promise<Config> {
	const { data, error } = await apiClient.POST("/api/config", {
		body: request,
	});
	if (error) {
		throw new Error(
			typeof error === "object" && "message" in error
				? String((error as Record<string, unknown>)["message"])
				: JSON.stringify(error),
		);
	}
	return data!;
}
