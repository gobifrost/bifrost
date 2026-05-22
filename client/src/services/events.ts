/**
 * Events API service using openapi-react-query pattern
 *
 * Manages event sources, subscriptions, events, and deliveries.
 * All mutations automatically invalidate relevant queries.
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types from OpenAPI spec
export type EventSource = components["schemas"]["EventSourceResponse"];
export type EventSourceCreate = components["schemas"]["EventSourceCreate"];
export type EventSourceUpdate = components["schemas"]["EventSourceUpdate"];
export type EventSourceType = components["schemas"]["EventSourceType"];

export type EventSubscription =
	components["schemas"]["EventSubscriptionResponse"];
export type EventSubscriptionCreate =
	components["schemas"]["EventSubscriptionCreate"];
export type EventSubscriptionUpdate =
	components["schemas"]["EventSubscriptionUpdate"];

export type Event = components["schemas"]["EventResponse"];
export type EventStatus = components["schemas"]["EventStatus"];

export type EventDelivery = components["schemas"]["EventDeliveryResponse"];
// EventDeliveryStatus is now a string to support "not_delivered" for subscriptions without deliveries
export type EventDeliveryStatus = EventDelivery["status"];

export type WebhookAdapter = components["schemas"]["WebhookAdapterInfo"];
export type WebhookSourceConfig = components["schemas"]["WebhookSourceConfig"];
export type WebhookSourceResponse =
	components["schemas"]["WebhookSourceResponse"];

export type RetryDeliveryResponse =
	components["schemas"]["RetryDeliveryResponse"];

export type DynamicValuesRequest =
	components["schemas"]["DynamicValuesRequest"];
export type DynamicValuesResponse =
	components["schemas"]["DynamicValuesResponse"];

export type TopicsRegistryResponse =
	components["schemas"]["TopicsRegistryResponse"];
export type TopicRegistryEntry = components["schemas"]["TopicRegistryEntry"];

// ============================================================================
// Topics
// ============================================================================

/**
 * Hook to fetch the topic registry (curated + in-use topics).
 * No auth required — used to populate the topic picker in CreateEventSourceDialog.
 */
export function useTopics() {
	return $api.useQuery("get", "/api/events/topics", {});
}

// ============================================================================
// Webhook Adapters
// ============================================================================

/**
 * Hook to fetch available webhook adapters
 */
export function useWebhookAdapters() {
	return $api.useQuery("get", "/api/events/adapters", {});
}

/**
 * Hook to fetch dynamic values for adapter config fields.
 * Used to populate dropdowns for fields with x-dynamic-values in config_schema.
 */
export function useDynamicValues(
	adapterName: string | undefined,
	operation: string | undefined,
	integrationId: string | undefined,
	currentConfig: Record<string, unknown>,
	enabled = true,
) {
	return $api.useQuery(
		"post",
		"/api/events/adapters/{adapter_name}/dynamic-values",
		{
			params: {
				path: { adapter_name: adapterName! },
			},
			body: {
				operation: operation!,
				integration_id: integrationId || undefined,
				current_config: currentConfig,
			},
		},
		{
			enabled: enabled && !!adapterName && !!operation,
			// Cache for 5 minutes
			staleTime: 5 * 60 * 1000,
		},
	);
}

// ============================================================================
// Event Sources
// ============================================================================

/**
 * Hook to fetch event sources with optional filtering
 */
export function useEventSources(params?: {
	sourceType?: EventSourceType;
	organizationId?: string;
	scope?: string;
	limit?: number;
	offset?: number;
}) {
	return $api.useQuery("get", "/api/events/sources", {
		params: {
			query: {
				source_type: params?.sourceType,
				organization_id: params?.organizationId,
				scope: params?.scope,
				limit: params?.limit ?? 100,
				offset: params?.offset ?? 0,
			},
		},
	});
}

/**
 * Hook to fetch a single event source
 */
export function useEventSource(sourceId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/events/sources/{source_id}",
		{
			params: {
				path: { source_id: sourceId! },
			},
		},
		{ enabled: !!sourceId },
	);
}

/**
 * Hook to create an event source
 */
export function useCreateEventSource() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/events/sources", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/events/sources"],
			});
		},
	});
}

/**
 * Hook to update an event source
 */
export function useUpdateEventSource() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/events/sources/{source_id}", {
		onSuccess: (_, variables) => {
			const sourceId = variables.params.path.source_id;
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/events/sources"],
			});
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/events/sources/{source_id}",
					{ params: { path: { source_id: sourceId } } },
				],
			});
		},
	});
}

/**
 * Hook to delete an event source
 */
export function useDeleteEventSource() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/events/sources/{source_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/events/sources"],
			});
		},
	});
}

// ============================================================================
// Event Subscriptions
// ============================================================================

/**
 * Hook to fetch subscriptions for an event source
 */
export function useSubscriptions(
	sourceId: string | undefined,
	params?: { limit?: number; offset?: number },
) {
	return $api.useQuery(
		"get",
		"/api/events/sources/{source_id}/subscriptions",
		{
			params: {
				path: { source_id: sourceId! },
				query: {
					limit: params?.limit ?? 100,
					offset: params?.offset ?? 0,
				},
			},
		},
		{ enabled: !!sourceId },
	);
}

/**
 * Hook to create a subscription
 */
export function useCreateSubscription() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/events/sources/{source_id}/subscriptions",
		{
			onSuccess: (_, variables) => {
				const sourceId = variables.params.path.source_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/events/sources/{source_id}/subscriptions",
						{ params: { path: { source_id: sourceId } } },
					],
				});
				// Also refresh source to update subscription count
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/events/sources/{source_id}",
						{ params: { path: { source_id: sourceId } } },
					],
				});
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/events/sources"],
				});
			},
		},
	);
}

/**
 * Hook to update a subscription
 */
export function useUpdateSubscription() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"patch",
		"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
		{
			onSuccess: () => {
				// Use partial key match to invalidate regardless of query params
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/events/sources/{source_id}/subscriptions",
					],
				});
			},
		},
	);
}

/**
 * Hook to delete a subscription
 */
export function useDeleteSubscription() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"delete",
		"/api/events/sources/{source_id}/subscriptions/{subscription_id}",
		{
			onSuccess: (_, variables) => {
				const sourceId = variables.params.path.source_id;
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/events/sources/{source_id}/subscriptions",
						{ params: { path: { source_id: sourceId } } },
					],
				});
				// Also refresh source to update subscription count
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/events/sources/{source_id}",
						{ params: { path: { source_id: sourceId } } },
					],
				});
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/events/sources"],
				});
			},
		},
	);
}

// ============================================================================
// Events
// ============================================================================

export interface EventFilters {
	status?: EventStatus;
	event_type?: string;
	since?: string; // ISO date string
	until?: string; // ISO date string
	limit?: number;
	offset?: number;
}

/**
 * Hook to fetch events for an event source with optional filters
 */
export function useEvents(sourceId: string | undefined, params?: EventFilters) {
	return $api.useQuery(
		"get",
		"/api/events/sources/{source_id}/events",
		{
			params: {
				path: { source_id: sourceId! },
				query: {
					status: params?.status,
					event_type: params?.event_type,
					since: params?.since,
					until: params?.until,
					limit: params?.limit ?? 100,
					offset: params?.offset ?? 0,
				},
			},
		},
		{ enabled: !!sourceId },
	);
}

/**
 * Hook to fetch a single event
 */
export function useEvent(eventId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/events/{event_id}",
		{
			params: {
				path: { event_id: eventId! },
			},
		},
		{ enabled: !!eventId },
	);
}

// ============================================================================
// Event Deliveries
// ============================================================================

/**
 * Hook to fetch deliveries for an event
 */
export function useDeliveries(eventId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/events/{event_id}/deliveries",
		{
			params: {
				path: { event_id: eventId! },
			},
		},
		{ enabled: !!eventId },
	);
}

/**
 * Hook to retry a failed delivery
 */
export function useRetryDelivery() {
	const queryClient = useQueryClient();

	return $api.useMutation(
		"post",
		"/api/events/deliveries/{delivery_id}/retry",
		{
			onSuccess: () => {
				// Invalidate all delivery-related queries
				queryClient.invalidateQueries({
					predicate: (query) =>
						query.queryKey[0] === "get" &&
						(query.queryKey[1] as string)?.includes("/deliveries"),
				});
				// Also invalidate events to refresh status
				queryClient.invalidateQueries({
					predicate: (query) =>
						query.queryKey[0] === "get" &&
						(query.queryKey[1] as string)?.includes("/events"),
				});
			},
		},
	);
}

/**
 * Hook to create a delivery for an existing event.
 * Used to retroactively send an event to a subscription added after the event arrived.
 */
export function useCreateDelivery() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/events/{event_id}/deliveries", {
		onSuccess: () => {
			// Invalidate all delivery-related queries
			queryClient.invalidateQueries({
				predicate: (query) =>
					query.queryKey[0] === "get" &&
					(query.queryKey[1] as string)?.includes("/deliveries"),
			});
			// Also invalidate events to refresh status
			queryClient.invalidateQueries({
				predicate: (query) =>
					query.queryKey[0] === "get" &&
					(query.queryKey[1] as string)?.includes("/events"),
			});
		},
	});
}
