/**
 * React hook for App Builder live updates via WebSocket
 *
 * Provides real-time updates for App Builder applications:
 * - Draft mode: Instant updates when MCP/editor modifies pages or components
 * - Live mode: "New Version Available" indicator when app is republished
 */

import { useEffect, useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
	webSocketService,
	type AppDraftUpdate,
	type AppPublishedUpdate,
} from "@/services/websocket";
import { useAppBuilderStore } from "@/stores/app-builder.store";

interface UseAppLiveUpdatesOptions {
	/** Application ID to monitor */
	appId: string | undefined;
	/** Whether viewing draft or live version */
	mode: "draft" | "live";
	/** Current version ID (used to detect if a new version has been published) */
	currentVersionId?: string;
	/** Whether to enable WebSocket subscriptions (default: true) */
	enabled?: boolean;
}

interface LastUpdate {
	userName: string;
	timestamp: Date;
}

/**
 * Hook for real-time App Builder updates
 *
 * @example
 * ```tsx
 * // Draft mode - auto-refresh on changes
 * const { lastUpdate, updateCounter } = useAppLiveUpdates({
 *   appId: app.id,
 *   mode: 'draft',
 *   enabled: true,
 * });
 *
 * // Live mode - show new version banner
 * const { newVersionAvailable, refreshApp } = useAppLiveUpdates({
 *   appId: app.id,
 *   mode: 'live',
 *   currentVersionId: app.published_at,
 * });
 * ```
 */
export function useAppLiveUpdates(options: UseAppLiveUpdatesOptions) {
	const { appId, mode, currentVersionId, enabled = true } = options;
	const queryClient = useQueryClient();
	const reset = useAppBuilderStore((state) => state.reset);

	const [lastUpdate, setLastUpdate] = useState<LastUpdate | null>(null);
	const [newVersionAvailable, setNewVersionAvailable] = useState(false);
	const [newVersionId, setNewVersionId] = useState<string | null>(null);
	// Counter that increments on each update - triggers useEffect dependencies
	const [updateCounter, setUpdateCounter] = useState(0);

	// Handle draft updates - invalidate relevant queries
	const handleDraftUpdate = useCallback(
		(update: AppDraftUpdate) => {
			// Don't process if disabled or wrong app
			if (!enabled || update.appId !== appId) return;

			// Update attribution display
			setLastUpdate({
				userName: update.userName,
				timestamp: new Date(update.timestamp),
			});

			// Clear attribution after 3 seconds
			setTimeout(() => setLastUpdate(null), 3000);

			// Increment counter to trigger data reload in consuming components
			setUpdateCounter((c) => c + 1);

			// Invalidate relevant queries based on entity type
			// Note: We use predicate-based matching because React Query stores the full
			// params object in the key, so simple array matching doesn't work.
			if (update.entityType === "page") {
				// Invalidate page list and specific page queries
				queryClient.invalidateQueries({
					predicate: (query) => {
						const key = query.queryKey;
						return (
							Array.isArray(key) &&
							key[0] === "get" &&
							(key[1] === "/api/applications/{app_id}/pages" ||
								key[1] ===
									"/api/applications/{app_id}/pages/{page_id}")
						);
					},
				});
			} else if (update.entityType === "component") {
				// Components are nested in pages, invalidate parent page and component queries
				queryClient.invalidateQueries({
					predicate: (query) => {
						const key = query.queryKey;
						return (
							Array.isArray(key) &&
							key[0] === "get" &&
							(key[1] ===
								"/api/applications/{app_id}/pages/{page_id}" ||
								key[1] ===
									"/api/applications/{app_id}/pages/{page_id}/components" ||
								key[1] ===
									"/api/applications/{app_id}/pages/{page_id}/components/{component_id}")
						);
					},
				});
			} else if (update.entityType === "app") {
				// App-level changes (navigation, global settings, etc.)
				queryClient.invalidateQueries({
					predicate: (query) => {
						const key = query.queryKey;
						return (
							Array.isArray(key) &&
							key[0] === "get" &&
							(key[1] === "/api/applications/{slug}" ||
								key[1] === "/api/applications")
						);
					},
				});
			}
		},
		[appId, enabled, queryClient],
	);

	// Handle publish events - show new version banner
	const handlePublished = useCallback(
		(update: AppPublishedUpdate) => {
			if (!enabled || update.appId !== appId) return;

			// Only show banner if we have a different version
			if (currentVersionId && update.newVersionId !== currentVersionId) {
				setNewVersionAvailable(true);
				setNewVersionId(update.newVersionId);

				// Also show who published
				setLastUpdate({
					userName: update.userName,
					timestamp: new Date(update.timestamp),
				});
			}
		},
		[appId, currentVersionId, enabled],
	);

	// Soft refresh function - invalidate all queries and reset store
	const refreshApp = useCallback(() => {
		if (!appId) return;

		// Invalidate all app-related queries using predicate matching
		queryClient.invalidateQueries({
			predicate: (query) => {
				const key = query.queryKey;
				return (
					Array.isArray(key) &&
					key[0] === "get" &&
					(key[1] === "/api/applications/{slug}" ||
						key[1] === "/api/applications" ||
						key[1] === "/api/applications/{app_id}/pages" ||
						key[1] === "/api/applications/{app_id}/pages/{page_id}")
				);
			},
		});

		// Reset the store state
		reset();

		// Clear the new version banner
		setNewVersionAvailable(false);
		setNewVersionId(null);
		setLastUpdate(null);
	}, [appId, queryClient, reset]);

	// Subscribe to WebSocket channels
	useEffect(() => {
		if (!appId || !enabled) return;

		const unsubscribers: (() => void)[] = [];

		const init = async () => {
			try {
				if (mode === "draft") {
					// Connect to draft channel
					await webSocketService.connectToAppDraft(appId);
					// Subscribe to draft updates
					unsubscribers.push(
						webSocketService.onAppDraftUpdate(
							appId,
							handleDraftUpdate,
						),
					);
					// Also subscribe to publish events so draft viewers know when it's published
					await webSocketService.connectToAppLive(appId);
					unsubscribers.push(
						webSocketService.onAppPublished(appId, handlePublished),
					);
				} else {
					// Live mode - only care about new publishes
					await webSocketService.connectToAppLive(appId);
					unsubscribers.push(
						webSocketService.onAppPublished(appId, handlePublished),
					);
				}
			} catch (error) {
				console.error(
					"[useAppLiveUpdates] Failed to connect to WebSocket:",
					error,
				);
			}
		};

		void init();

		return () => {
			unsubscribers.forEach((unsub) => unsub());
			// Unsubscribe from channels
			if (mode === "draft") {
				void webSocketService.unsubscribe(`app:draft:${appId}`);
			}
			void webSocketService.unsubscribe(`app:live:${appId}`);
		};
	}, [appId, mode, enabled, handleDraftUpdate, handlePublished]);

	return {
		/** Last update attribution (who made the change) */
		lastUpdate,
		/** Whether a new published version is available */
		newVersionAvailable,
		/** The new version ID (if available) */
		newVersionId,
		/** Refresh the app to load the new version */
		refreshApp,
		/** Counter that increments on each update - use as useEffect dependency to trigger reloads */
		updateCounter,
	};
}
