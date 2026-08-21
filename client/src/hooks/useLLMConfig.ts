/**
 * Hooks for checking LLM configuration status
 *
 * Used to determine if AI chat is available and configured.
 * Only works from the selected Platform boundary when configs can be read.
 */

import { $api } from "@/lib/api-client";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

/**
 * Hook to check if LLM provider is configured
 *
 * Returns:
 * - isConfigured: whether the LLM provider is set up with an API key
 * - isLoading: whether the query is in progress
 * - config: the full config response (for admins only)
 */
export function useLLMConfig() {
	const {
		hasSelectedCapability,
		isLoading: permissionsLoading,
		selectedTarget,
	} = useAuthorizationBoundary();
	const canReadConfig =
		selectedTarget?.kind === "platform" &&
		(hasSelectedCapability("configs.read") ||
			hasSelectedCapability("configs.readwrite"));

	const {
		data: config,
		isLoading: configLoading,
		error,
	} = $api.useQuery("get", "/api/admin/llm/config", undefined, {
		enabled: canReadConfig && !permissionsLoading,
		// Cache for 5 minutes
		staleTime: 5 * 60 * 1000,
		// Don't retry on 404 (not configured)
		retry: false,
	});

	// Without platform config read, callers cannot check the admin endpoint.
	// They'll get an error when trying to chat if not configured
	const isConfigured = canReadConfig
		? (config?.is_configured ?? false)
		: null; // null means "unknown"

	return {
		isConfigured,
		isPlatformAdmin: canReadConfig,
		canReadConfig,
		isLoading: permissionsLoading || (canReadConfig && configLoading),
		config,
		error,
	};
}

/**
 * Hook to fetch available models from the configured LLM provider
 */
export function useLLMModels() {
	const {
		hasSelectedCapability,
		isLoading: permissionsLoading,
		selectedTarget,
	} = useAuthorizationBoundary();
	const canReadConfig =
		selectedTarget?.kind === "platform" &&
		(hasSelectedCapability("configs.read") ||
			hasSelectedCapability("configs.readwrite"));

	const {
		data,
		isLoading: modelsLoading,
		error,
	} = $api.useQuery("get", "/api/admin/llm/models", undefined, {
		enabled: canReadConfig && !permissionsLoading,
		staleTime: 10 * 60 * 1000, // Cache for 10 minutes
		retry: false,
	});

	return {
		models: data?.models ?? [],
		provider: data?.provider,
		isLoading: permissionsLoading || (canReadConfig && modelsLoading),
		error,
	};
}
