/**
 * Agent Management API hooks
 *
 * Provides hooks for:
 * - Listing and fetching agents
 * - Creating, updating, and deleting agents
 * - Reading agent tools and delegations (membership changes go through
 *   the full-agent PUT via `useUpdateAgent`)
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type AgentPublic = components["schemas"]["AgentPublic"];
export type AgentSummary = components["schemas"]["AgentSummary"];
export type AgentCreate = components["schemas"]["AgentCreate"];
export type AgentUpdate = components["schemas"]["AgentUpdate"];

/** Helper to extract error message from API error response */
function getErrorMessage(error: unknown, fallback: string): string {
	if (typeof error === "object" && error && "message" in error) {
		return String((error as Record<string, unknown>)["message"]);
	}
	if (error instanceof Error) {
		return error.message;
	}
	return fallback;
}

// ==================== Query Hooks ====================

/**
 * Hook to fetch all agents with optional organization filtering
 * @param filterScope - Filter scope: undefined = all, null = global only, string = org UUID
 *
 * The scope query param controls filtering:
 * - Omitted (undefined): show all agents (superusers) / user's org + global (org users)
 * - "global": show only global agents (org_id IS NULL)
 * - UUID string: show that org's agents + global agents
 */
export function useAgents(
	filterScope?: string | null,
	options?: { includeInactive?: boolean; includeStats?: boolean },
) {
	// Build query params - scope is the new filter parameter
	const queryParams: Record<string, string | boolean | undefined> = {};
	if (filterScope === null) {
		// null means "global only"
		queryParams.scope = "global";
	} else if (filterScope !== undefined) {
		// UUID string means specific org
		queryParams.scope = filterScope;
	}
	// undefined = don't send scope (show all)
	if (options?.includeInactive) {
		// Default server-side is active_only=true; opt in to paused agents too.
		queryParams.active_only = false;
	}
	if (options?.includeStats) {
		queryParams.include_stats = true;
	}

	return $api.useQuery("get", "/api/agents", {
		params: {
			// Type assertion needed until types are regenerated
			query:
				Object.keys(queryParams).length > 0 ? queryParams : undefined,
		} as {
			query?: {
				category?: string | null;
				active_only?: boolean;
				include_stats?: boolean;
				scope?: string;
			};
		},
	});
}

/** Hook to fetch a specific agent */
export function useAgent(agentId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agents/{agent_id}",
		{ params: { path: { agent_id: agentId ?? "" } } },
		{ enabled: !!agentId, staleTime: 60_000 },
	);
}

/** Hook to fetch agent tools */
export function useAgentTools(agentId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agents/{agent_id}/tools",
		{ params: { path: { agent_id: agentId ?? "" } } },
		{ enabled: !!agentId },
	);
}

/** Hook to fetch agent delegations */
export function useAgentDelegations(agentId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agents/{agent_id}/delegations",
		{ params: { path: { agent_id: agentId ?? "" } } },
		{ enabled: !!agentId },
	);
}

// ==================== Mutation Hooks ====================

/** Hook to create a new agent */
export function useCreateAgent() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/agents", {
		onSuccess: (_data, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/agents"] });
			const name = (variables.body as AgentCreate)?.name;
			toast.success("Agent created", {
				description: `Agent "${name}" has been created`,
			});
		},
		onError: (error) => {
			toast.error("Failed to create agent", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/** Hook to update an agent */
export function useUpdateAgent() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/agents/{agent_id}", {
		onSuccess: (_data, variables) => {
			const agentId = (variables.params as { path: { agent_id: string } })
				.path.agent_id;
			queryClient.invalidateQueries({ queryKey: ["get", "/api/agents"] });
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/agents/{agent_id}",
					{ params: { path: { agent_id: agentId } } },
				],
			});
			toast.success("Agent updated");
		},
		onError: (error) => {
			toast.error("Failed to update agent", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/** Hook to delete an agent (soft delete) */
export function useDeleteAgent() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/agents/{agent_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/agents"] });
			toast.success("Agent deleted");
		},
		onError: (error) => {
			toast.error("Failed to delete agent", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

// Tool and delegation membership is now managed via the full-agent PUT
// (`useUpdateAgent`) — callers send the complete `tool_ids` /
// `delegated_agent_ids` lists with adds/removes already applied.
