import { useRef, useCallback } from "react";
import {
	fileService,
	FileConflictError,
	type FileConflictResponse,
} from "@/services/fileService";
import type { ConflictReason, FileDiagnostic } from "@/stores/editorStore";
import { toast } from "sonner";

interface SaveQueueEntry {
	onError?: (() => void) | undefined;
	filePath: string;
	content: string;
	encoding: "utf-8" | "base64";
	timestamp: number;
	currentEtag?: string | undefined; // Expected etag for conflict detection
	index?: boolean | undefined; // If true, inject IDs into decorators
	debounceTimer?: NodeJS.Timeout | undefined;
	onComplete?:
		| ((
				newEtag: string,
				newContent?: string,
				needsIndexing?: boolean,
				diagnostics?: FileDiagnostic[],
		  ) => void)
		| undefined;
	onConflict?:
		| ((
				reason: ConflictReason,
				conflictData?: FileConflictResponse,
		  ) => void)
		| undefined;
}

/**
 * Manages a queue of pending file saves with debouncing.
 * Ensures saves complete even when switching tabs.
 *
 * Uses 1-second debounce (Google Docs/VS Code style) and
 * processes saves sequentially to prevent conflicts.
 */
const alwaysSave = () => true;

export function useSaveQueue(
	shouldSave: (filePath: string) => boolean = alwaysSave,
) {
	const saveQueueRef = useRef<Map<string, SaveQueueEntry>>(new Map());
	const savingRef = useRef(false);

	/**
	 * Execute a pending save for a file
	 */
	const executeSave = useCallback(
		async (
			entry: SaveQueueEntry,
		): Promise<{
			success: boolean;
			etag?: string;
			content?: string;
			contentModified?: boolean;
			needsIndexing?: boolean;
			diagnostics?: FileDiagnostic[];
		}> => {
			try {
				// Pass etag for conflict detection and index flag
				const response = await fileService.writeFile(
					entry.filePath,
					entry.content,
					entry.encoding,
					entry.currentEtag,
					entry.index ?? false,
				);
				return {
					success: true,
					etag: response.etag,
					content: response.content,
					contentModified: response.content_modified ?? false,
					needsIndexing: response.needs_indexing ?? false,
					diagnostics: response.diagnostics as
						FileDiagnostic[] | undefined,
				};
			} catch (error) {
				// Handle conflict errors specially
				if (error instanceof FileConflictError) {
					console.warn(
						`[SaveQueue] Conflict detected for ${entry.filePath}:`,
						error.conflictData.reason,
					);
					if (
						entry.onConflict &&
						shouldSave(entry.filePath) &&
						saveQueueRef.current.get(entry.filePath) === entry
					) {
						// Pass full conflict data for deactivation conflicts
						entry.onConflict(
							error.conflictData.reason as ConflictReason,
							error.conflictData,
						);
					}
					return { success: false };
				}

				console.error(
					`[SaveQueue] Failed to save ${entry.filePath}:`,
					error,
				);
				if (
					shouldSave(entry.filePath) &&
					saveQueueRef.current.get(entry.filePath) === entry
				)
					entry.onError?.();
				toast.error("Failed to save file", {
					id: `file-save-error:${entry.filePath}`,
					description: `${entry.filePath}: ${error instanceof Error ? error.message : String(error)}`,
				});
				return { success: false };
			}
		},
		[shouldSave],
	);

	/**
	 * Process the next item in the queue
	 */
	const processQueue = useCallback(
		async function processQueueInternal() {
			if (savingRef.current) return;

			const queue = saveQueueRef.current;
			const entries = Array.from(queue.values());

			for (const entry of entries) {
				// Skip if still debouncing
				if (entry.debounceTimer) continue;

				// Discard queued edits whose editor tab has been closed.
				if (!shouldSave(entry.filePath)) {
					queue.delete(entry.filePath);
					continue;
				}

				// Mark as saving
				savingRef.current = true;

				// Execute save
				const result = await executeSave(entry);
				const current = queue.get(entry.filePath);
				if (
					result.success &&
					result.etag &&
					current &&
					current !== entry &&
					current.currentEtag === entry.currentEtag
				) {
					current.currentEtag = result.etag;
				}

				// Call completion callback if provided
				// If content was modified (e.g., IDs injected), pass new content
				// Also pass needsIndexing flag for deferred indexing flow
				// Pass diagnostics (syntax errors, etc.) for editor display
				if (
					result.success &&
					current === entry &&
					entry.onComplete &&
					result.etag &&
					shouldSave(entry.filePath)
				) {
					entry.onComplete(
						result.etag,
						result.contentModified ? result.content : undefined,
						result.needsIndexing,
						result.diagnostics,
					);
				}

				// A newer edit may have replaced this entry while the request was in flight.
				if (queue.get(entry.filePath) === entry)
					queue.delete(entry.filePath);

				savingRef.current = false;

				// Process next item recursively
				if (queue.size > 0) {
					void processQueueInternal();
				}
				break;
			}
		},
		[executeSave, shouldSave],
	);

	/**
	 * Enqueue a save with 1-second debounce
	 *
	 * @param onComplete - Called when save completes. If server modified content
	 *                     (e.g., injected IDs), newContent will be provided.
	 *                     Also receives needsIndexing flag for deferred indexing.
	 *                     Also receives diagnostics (syntax errors, etc.) for editor display.
	 * @param index - If true, inject IDs into decorators. If false (default), detect only.
	 */
	const enqueueSave = useCallback(
		(
			filePath: string,
			content: string,
			encoding: "utf-8" | "base64" = "utf-8",
			currentEtag?: string,
			onComplete?: (
				newEtag: string,
				newContent?: string,
				needsIndexing?: boolean,
				diagnostics?: FileDiagnostic[],
			) => void,
			onConflict?: (
				reason: ConflictReason,
				conflictData?: FileConflictResponse,
			) => void,
			index: boolean = false,
			onError?: () => void,
		) => {
			const queue = saveQueueRef.current;
			const existing = queue.get(filePath);

			// Clear existing debounce timer
			if (existing?.debounceTimer) {
				clearTimeout(existing.debounceTimer);
			}

			// Create or update queue entry
			const entry: SaveQueueEntry = {
				filePath,
				content,
				encoding,
				currentEtag,
				index,
				timestamp: Date.now(),
				onComplete,
				onConflict,
				onError,
				debounceTimer: setTimeout(() => {
					// Clear timer and process queue
					const currentEntry = queue.get(filePath);
					if (currentEntry) {
						currentEntry.debounceTimer = undefined;
						processQueue();
					}
				}, 1000), // 1 second debounce (VS Code/Google Docs style)
			};

			queue.set(filePath, entry);
		},
		[processQueue],
	);

	/**
	 * Force immediate save (bypasses debounce)
	 */
	const forceSave = useCallback(
		async (
			filePath: string,
			content: string,
			encoding: "utf-8" | "base64" = "utf-8",
			currentEtag?: string,
		) => {
			const queue = saveQueueRef.current;
			const existing = queue.get(filePath);

			// Clear debounce if exists
			if (existing?.debounceTimer) {
				clearTimeout(existing.debounceTimer);
			}

			// Create entry without debounce
			const entry: SaveQueueEntry = {
				filePath,
				content,
				encoding,
				currentEtag,
				timestamp: Date.now(),
			};

			queue.set(filePath, entry);

			// Process immediately
			await processQueue();
		},
		[processQueue],
	);

	/**
	 * Check if a file has pending saves
	 */
	const hasPendingSave = useCallback((filePath: string) => {
		return saveQueueRef.current.has(filePath);
	}, []);

	/**
	 * Get pending save count
	 */
	const getPendingCount = useCallback(() => {
		return saveQueueRef.current.size;
	}, []);

	/**
	 * Wait for all pending saves to complete
	 * Polls every 50ms until queue is empty
	 */
	const waitForPendingSaves = useCallback(async (): Promise<void> => {
		const maxWaitTime = 10000; // 10 second max wait
		const startTime = Date.now();

		while (saveQueueRef.current.size > 0 || savingRef.current) {
			if (Date.now() - startTime > maxWaitTime) {
				console.warn("[SaveQueue] Timeout waiting for pending saves");
				break;
			}
			await new Promise((resolve) => setTimeout(resolve, 50));
		}
	}, []);

	return {
		enqueueSave,
		forceSave,
		hasPendingSave,
		getPendingCount,
		waitForPendingSaves,
	};
}
