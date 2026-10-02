/**
 * Editor Search API service
 * Paged grep over workspace and Solution source (follow next_cursor for more)
 * Uses auto-generated types from OpenAPI spec
 */

import { apiClient, $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Auto-generated types from OpenAPI spec
export type SearchRequest = components["schemas"]["SearchRequest"];
export type SearchMatch = components["schemas"]["SearchMatch"];
export type SearchFileHit = components["schemas"]["SearchFileHit"];
export type SearchResponse = components["schemas"]["SearchResponse"];

/**
 * Hook to search file contents (for React components)
 */
export function useSearchFiles() {
	return $api.useMutation("post", "/api/files/search");
}

/**
 * Imperative search service for use in callbacks
 */
export const searchService = {
	async searchFiles(request: SearchRequest): Promise<SearchResponse> {
		const { data, error } = await apiClient.POST("/api/files/search", {
			body: request,
		});

		if (error) {
			throw new Error(
				(error as { detail?: string }).detail ||
					"Failed to search files",
			);
		}

		return data;
	},
};
