/**
 * OAuth SSO Configuration API service
 *
 * Manages OAuth SSO provider configurations (Microsoft, Google, OIDC)
 * stored in the database. Platform admin only.
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types from OpenAPI spec (will be available after type generation)
export type OAuthProviderConfig =
	components["schemas"]["OAuthProviderConfigResponse"];
export type OAuthConfigList = components["schemas"]["OAuthConfigListResponse"];
export type OAuthConfigTestResponse =
	components["schemas"]["OAuthConfigTestResponse"];
export type OAuthLoginPreference =
	components["schemas"]["OAuthLoginPreference"];
export type MicrosoftOAuthConfig =
	components["schemas"]["MicrosoftOAuthConfigRequest"];
export type GoogleOAuthConfig =
	components["schemas"]["GoogleOAuthConfigRequest"];
export type OIDCConfig = components["schemas"]["OIDCConfigRequest"];

export type OAuthProvider = "microsoft" | "google" | "oidc";

/**
 * Hook to fetch all OAuth provider configurations
 */
export function useOAuthConfigs() {
	return $api.useQuery("get", "/api/settings/oauth");
}

/**
 * Hook to update whether login should first redirect to a preferred provider.
 */
export function useUpdateOAuthLoginPreference() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/settings/oauth/login-preference", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/auth/status"],
			});
		},
	});
}

/**
 * Hook to fetch a single OAuth provider configuration
 */
export function useOAuthConfig(provider: OAuthProvider) {
	return $api.useQuery("get", "/api/settings/oauth/{provider}", {
		params: { path: { provider } },
	});
}

/**
 * Hook to update Microsoft OAuth configuration
 */
export function useUpdateMicrosoftConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/settings/oauth/microsoft", {
		onSuccess: () => {
			// Invalidate OAuth config queries
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth/{provider}"],
			});
			// Also invalidate auth status (available providers)
			queryClient.invalidateQueries({
				queryKey: ["get", "/auth/status"],
			});
		},
	});
}

/**
 * Hook to update Google OAuth configuration
 */
export function useUpdateGoogleConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/settings/oauth/google", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth/{provider}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/auth/status"],
			});
		},
	});
}

/**
 * Hook to update OIDC provider configuration
 */
export function useUpdateOIDCConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/settings/oauth/oidc", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth/{provider}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/auth/status"],
			});
		},
	});
}

/**
 * Hook to delete an OAuth provider configuration
 */
export function useDeleteOAuthConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/settings/oauth/{provider}", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/settings/oauth/{provider}"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/auth/status"],
			});
		},
	});
}

/**
 * Hook to test OAuth provider configuration
 */
export function useTestOAuthConfig() {
	return $api.useMutation("post", "/api/settings/oauth/{provider}/test");
}
