/**
 * React hook for Code Engine app live updates via WebSocket
 *
 * Provides real-time updates for Code Engine applications:
 * - Receives file create/update/delete events with full content
 * - Maintains local cache of file contents for instant preview
 * - Provides attribution display (who made the change)
 */

import { useEffect, useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
	webSocketService,
	type AppCodeFileUpdate,
	type AppPublishedUpdate,
} from "@/services/websocket";

export interface CodeFile {
	path: string;
	source: string | null;
	compiled: string | null;
}

export interface LastUpdate {
	userName: string;
	timestamp: Date;
	action: "create" | "update" | "delete";
	path: string;
}

interface UseAppCodeUpdatesOptions {
	/** Application ID to monitor */
	appId: string | undefined;
	/** Whether to enable WebSocket subscriptions (default: true) */
	enabled?: boolean;
	/** Callback when a file update is received (for triggering side effects) */
	onUpdate?: (update: LastUpdate) => void;
}

/**
 * Hook for real-time Code Engine app updates
 *
 * @example
 * ```tsx
 * const { files, lastUpdate, updateCounter } = useAppCodeUpdates({
 *   appId: app.id,
 *   enabled: true,
 * });
 *
 * // Access file content
 * const indexSource = files.get('pages/index')?.source;
 *
 * // Or use the getFile helper
 * const layoutSource = getFile('_layout')?.source;
 * ```
 */
export function useAppCodeUpdates(options: UseAppCodeUpdatesOptions) {
	const { appId, enabled = true, onUpdate } = options;
	const queryClient = useQueryClient();

	// Local cache of file contents (path -> CodeFile)
	const [files, setFiles] = useState<Map<string, CodeFile>>(new Map());
	const [lastUpdate, setLastUpdate] = useState<LastUpdate | null>(null);
	const [newVersionAvailable, setNewVersionAvailable] = useState(false);
	// Counter that increments on each update - triggers useEffect dependencies
	const [updateCounter, setUpdateCounter] = useState(0);

	// Track if we've initialized (loaded initial files from API)
	const [isInitialized, setIsInitialized] = useState(false);

	// Handle code file updates
	const handleCodeFileUpdate = useCallback(
		(update: AppCodeFileUpdate) => {
			// Don't process if disabled or wrong app
			if (!enabled || update.appId !== appId) return;

			const lastUpdateData: LastUpdate = {
				userName: update.userName,
				timestamp: new Date(update.timestamp),
				action: update.action,
				path: update.path,
			};

			// Update attribution display
			setLastUpdate(lastUpdateData);

			// Clear attribution after 3 seconds
			setTimeout(() => setLastUpdate(null), 3000);

			// Increment counter to notify consumers
			setUpdateCounter((c) => c + 1);

			// Update local file cache
			setFiles((prev) => {
				const next = new Map(prev);

				if (update.action === "delete") {
					next.delete(update.path);
				} else {
					// create or update
					next.set(update.path, {
						path: update.path,
						source: update.source,
						compiled: update.compiled,
					});
				}

				return next;
			});

			// Call update callback (for triggering side effects like file tree refresh)
			onUpdate?.(lastUpdateData);
		},
		[appId, enabled, onUpdate],
	);

	// Handle publish events - show new version banner
	const handlePublished = useCallback(
		(update: AppPublishedUpdate) => {
			if (!enabled || update.appId !== appId) return;
			setNewVersionAvailable(true);

			// Show who published
			setLastUpdate({
				userName: update.userName,
				timestamp: new Date(update.timestamp),
				action: "update",
				path: "published",
			});
			void queryClient.invalidateQueries({
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
		},
		[appId, enabled, queryClient],
	);

	// Helper to get a file by path
	const getFile = useCallback(
		(path: string): CodeFile | undefined => {
			return files.get(path);
		},
		[files],
	);

	// Initialize cache from an array of files (call after fetching from API)
	const initializeFiles = useCallback((initialFiles: CodeFile[]) => {
		setFiles(new Map(initialFiles.map((f) => [f.path, f])));
		setIsInitialized(true);
	}, []);

	// Clear the cache and new version banner
	const reset = useCallback(() => {
		setFiles(new Map());
		setNewVersionAvailable(false);
		setLastUpdate(null);
		setIsInitialized(false);
	}, []);

	// Subscribe to WebSocket channels
	useEffect(() => {
		if (!appId || !enabled) return;

		const unsubscribers: (() => void)[] = [];

		const init = async () => {
			try {
				// Connect to draft channel for code file updates
				await webSocketService.connectToAppDraft(appId);

				// Subscribe to code file updates
				unsubscribers.push(
					webSocketService.onAppCodeFileUpdate(
						appId,
						handleCodeFileUpdate,
					),
				);

				// Also subscribe to publish events
				await webSocketService.connectToAppLive(appId);
				unsubscribers.push(
					webSocketService.onAppPublished(appId, handlePublished),
				);
			} catch (error) {
				console.error(
					"[useAppCodeUpdates] Failed to connect to WebSocket:",
					error,
				);
			}
		};

		init();

		return () => {
			unsubscribers.forEach((unsub) => unsub());
			// Unsubscribe from channels
			webSocketService.unsubscribe(`app:draft:${appId}`);
			webSocketService.unsubscribe(`app:live:${appId}`);
		};
	}, [appId, enabled, handleCodeFileUpdate, handlePublished]);

	return {
		/** Map of file path to file content */
		files,
		/** Get a specific file by path */
		getFile,
		/** Initialize the file cache from API response */
		initializeFiles,
		/** Last update attribution (who made the change) */
		lastUpdate,
		/** Whether a new published version is available */
		newVersionAvailable,
		/** Counter that increments on each update - use as useEffect dependency */
		updateCounter,
		/** Reset the cache and state */
		reset,
		/** Whether the cache has been initialized */
		isInitialized,
	};
}
