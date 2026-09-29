import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";

const DATE_SUFFIX = /-\d{4}(?:-\d{2}-\d{2}|\d{4})$/;

export async function getModelDisplayNames(): Promise<Record<string, string>> {
	const { data, error } = await apiClient.GET("/api/model-catalog/names");
	if (error || !data) throw new Error("Failed to load model names");
	return data.names;
}

/**
 * Catalog display name for a stored model id, or the id itself when the
 * catalog does not know it. Dated snapshot ids fall back to their undated
 * name ("claude-haiku-4-5-20251001" -> "Claude Haiku 4.5").
 */
export function displayModelName(
	names: Record<string, string> | undefined,
	model: string,
): string {
	if (!names) return model;
	return names[model] ?? names[model.replace(DATE_SUFFIX, "")] ?? model;
}

export function useModelDisplayName(): (model: string) => string {
	const query = useQuery({
		queryKey: ["model-catalog", "names"],
		queryFn: getModelDisplayNames,
		staleTime: 10 * 60 * 1000,
	});
	const names = query.data;
	return useCallback((model: string) => displayModelName(names, model), [names]);
}
