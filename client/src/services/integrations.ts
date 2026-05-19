/**
 * Integrations API service using openapi-react-query pattern
 *
 * All mutations automatically invalidate relevant queries so components
 * reading from useIntegration() will re-render with fresh data.
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types from OpenAPI spec
export type Integration = components["schemas"]["IntegrationResponse"];
export type IntegrationDetail =
	components["schemas"]["IntegrationDetailResponse"];
export type IntegrationMapping =
	components["schemas"]["IntegrationMappingResponse"];
export type OAuthConfigSummary = components["schemas"]["OAuthConfigSummary"];
export type ConfigSchemaItem = components["schemas"]["ConfigSchemaItem"];
export type IntegrationCreate = components["schemas"]["IntegrationCreate"];
export type IntegrationUpdate = components["schemas"]["IntegrationUpdate"];
export type IntegrationMappingCreate =
	components["schemas"]["IntegrationMappingCreate"];
export type IntegrationMappingUpdate =
	components["schemas"]["IntegrationMappingUpdate"];
export type MappingAuthorizeResponse =
	components["schemas"]["MappingAuthorizeResponse"];

/**
 * Hook to fetch all integrations
 */
export function useIntegrations() {
	return $api.useQuery("get", "/api/integrations");
}

/**
 * Hook to fetch a single integration (includes mappings)
 */
export function useIntegration(integrationId: string) {
	return $api.useQuery("get", "/api/integrations/{integration_id}", {
		params: {
			path: { integration_id: integrationId },
		},
	});
}

/**
 * Hook to create an integration
 */
export function useCreateIntegration() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/integrations", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/integrations"],
			});
		},
	});
}

/**
 * Hook to update an integration
 */
export function useUpdateIntegration() {
	const queryClient = useQueryClient();

	return $api.useMutation("put", "/api/integrations/{integration_id}", {
		onSuccess: (_, variables) => {
			const integrationId = variables.params.path.integration_id;
			// Invalidate both the list and the specific integration
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/integrations"],
			});
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/integrations/{integration_id}",
					{ params: { path: { integration_id: integrationId } } },
				],
			});
		},
	});
}

/**
 * Hook to delete an integration
 */
export function useDeleteIntegration() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/integrations/{integration_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/integrations"],
			});
		},
	});
}

/**
 * Hook to fetch mappings for an integration
 */
export function useIntegrationMappings(integrationId: string) {
	return $api.useQuery("get", "/api/integrations/{integration_id}/mappings", {
		params: {
			path: { integration_id: integrationId },
		},
	});
}

/**
 * Hook to create an integration mapping
 */
export function useCreateMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/mappings",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				// Invalidate the integration detail (which includes mappings)
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to update an integration mapping
 */
export function useUpdateMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"put",
		"/api/integrations/{integration_id}/mappings/{mapping_id}",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				// Invalidate the integration detail (which includes mappings)
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to delete an integration mapping
 */
export function useDeleteMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"delete",
		"/api/integrations/{integration_id}/mappings/{mapping_id}",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				// Invalidate the integration detail (which includes mappings)
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to update integration default config values
 */
export function useUpdateIntegrationConfig() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"put",
		"/api/integrations/{integration_id}/config",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				// Invalidate the integration detail to refresh config
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to generate SDK from OpenAPI spec
 */
export function useGenerateSDK() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/generate-sdk",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				// Invalidate the integration detail
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to batch upsert integration mappings
 */
export function useBatchUpsertMappings() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/mappings/batch",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

// Re-export test response type
export type IntegrationTestResponse =
	components["schemas"]["IntegrationTestResponse"];

/**
 * Hook to test integration connection
 * Tests connectivity by calling a simple SDK method
 */
export function useTestIntegration() {
	return $api.useMutation("post", "/api/integrations/{integration_id}/test");
}

/**
 * Hook to begin OAuth authorize flow for a specific mapping.
 * Returns an authorization URL the caller should redirect the user to.
 */
export function useAuthorizeMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to disconnect (revoke) the OAuth token for a specific mapping.
 */
export function useDisconnectMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to proactively refresh a specific mapping's OAuth token.
 */
export function useRefreshMapping() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/refresh",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to set the entity_id_source on an integration's OAuth provider.
 * Optionally backfills a triggering mapping's entity_id.
 */
export function useSetEntityIdSource() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"patch",
		"/api/integrations/{integration_id}/oauth/entity_id_source",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}

/**
 * Hook to clear the entity_id_source on an integration's OAuth provider.
 * When clearMappings=true, also clears entity_id on every mapping under
 * this integration so they re-capture on reconnect.
 */
export function useResetEntityIdSource() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"delete",
		"/api/integrations/{integration_id}/oauth/entity_id_source",
		{
			onSuccess: (_, variables) => {
				const integrationId = variables.params.path.integration_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/integrations/{integration_id}/mappings",
						{ params: { path: { integration_id: integrationId } } },
					],
				});
			},
		},
	);
}
