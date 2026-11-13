import { useEffect, useCallback, useRef } from "react";
import { useEditorStore } from "@/stores/editorStore";
import { useSaveQueue } from "./useSaveQueue";
import { fileService, FileConflictError } from "@/services/fileService";
import type { ConflictReason, FileDiagnostic } from "@/stores/editorStore";
import { useReloadWorkflowFile } from "./useWorkflows";

/**
 * Auto-save hook with 1-second debounce and save queue.
 * Ensures saves complete even when switching tabs (VS Code-like behavior).
 *
 * For Python files:
 * - Normal save does NOT inject IDs (fast path)
 * - If backend returns needs_indexing=true, shows "Indexing..." overlay
 * - Waits for any pending saves to complete (protects user data)
 * - Triggers indexing with index=true parameter
 * - Updates editor with indexed content
 */
function isFileStillOpen(filePath: string) {
	return useEditorStore
		.getState()
		.tabs.some((tab) => tab.file.path === filePath);
}

function getTabForPath(filePath: string) {
	const tabs = useEditorStore.getState().tabs;
	const tabIndex = tabs.findIndex((tab) => tab.file.path === filePath);

	if (tabIndex < 0) {
		return null;
	}

	return { tabIndex, tab: tabs[tabIndex] };
}

export function useAutoSave() {
	// Subscribe to tabs and activeTabIndex directly (not getters!)
	const tabs = useEditorStore((state) => state.tabs);
	const activeTabIndex = useEditorStore((state) => state.activeTabIndex);
	const setSaveState = useEditorStore((state) => state.setSaveState);
	const setConflictState = useEditorStore((state) => state.setConflictState);
	const setDiagnostics = useEditorStore((state) => state.setDiagnostics);
	const setIndexing = useEditorStore((state) => state.setIndexing);
	const updateTabContent = useEditorStore((state) => state.updateTabContent);
	const setPendingWorkflowConflict = useEditorStore(
		(state) => state.setPendingWorkflowConflict,
	);
	const setPendingDeactivationConflict = useEditorStore(
		(state) => state.setPendingDeactivationConflict,
	);

	// Compute active tab values from subscribed state
	const activeTab =
		activeTabIndex >= 0 && activeTabIndex < tabs.length
			? tabs[activeTabIndex]
			: null;

	const openFile = activeTab?.file || null;
	const fileContent = activeTab?.content || "";
	const unsavedChanges = activeTab?.unsavedChanges || false;
	const encoding = activeTab?.encoding || "utf-8";
	const currentEtag = activeTab?.etag;
	const saveState = activeTab?.saveState;

	const { enqueueSave, waitForPendingSaves } = useSaveQueue(isFileStillOpen);
	const { mutate: reloadWorkflowFile } = useReloadWorkflowFile();

	// Track if we're currently indexing to prevent re-triggering
	const indexingRef = useRef(false);

	// Auto-save with 1-second debounce using save queue
	useEffect(() => {
		// Only enqueue if we have unsaved changes and not in conflict.
		if (!unsavedChanges || !openFile || saveState === "conflict") {
			return;
		}

		const saveFilePath = openFile.path;
		const initialTabState = getTabForPath(saveFilePath);
		if (!initialTabState) {
			return;
		}

		// Set dirty state immediately (prevent infinite loop by checking current state)
		if (saveState !== "dirty") {
			setSaveState(initialTabState.tabIndex, "dirty");
		}

		// Update save state to saving after 950ms (visual feedback before save)
		const savingTimer = setTimeout(() => {
			const latestTabState = getTabForPath(saveFilePath);
			if (latestTabState) {
				setSaveState(latestTabState.tabIndex, "saving");
			}
		}, 950);

		const isPythonFile = openFile.name.endsWith(".py");

		// Enqueue save with completion and conflict callbacks
		// index=false: detect if IDs needed, don't inject yet
		enqueueSave(
			saveFilePath,
			fileContent,
			encoding,
			currentEtag,
			async (newEtag, newContent, needsIndexing, diagnostics) => {
				const currentTabState = getTabForPath(saveFilePath);
				if (!currentTabState) {
					return;
				}

				const { tabIndex: currentTabIndex } = currentTabState;

				// Store diagnostics in tab state for CodeEditor to display
				setDiagnostics(currentTabIndex, diagnostics);

				// If server modified content (e.g., injected IDs), update editor buffer
				if (newContent) {
					updateTabContent(currentTabIndex, newContent, newEtag);
				} else {
					// No content modification, just update etag and state
					const state = useEditorStore.getState();
					const newTabs = [...state.tabs];
					if (newTabs[currentTabIndex]) {
						newTabs[currentTabIndex] = {
							...newTabs[currentTabIndex]!,
							etag: newEtag,
							unsavedChanges: false,
							saveState: "saved",
						};
						useEditorStore.setState({ tabs: newTabs });
					}
				}

				// Handle deferred indexing for Python files
				if (isPythonFile && needsIndexing && !indexingRef.current) {
					indexingRef.current = true;

					try {
						// Show indexing overlay
						setIndexing(true, "Indexing workflow...");

						// Wait for any pending auto-saves to complete
						// This protects user data - don't index until latest changes are saved
						await waitForPendingSaves();

						const latestTabState = getTabForPath(saveFilePath);
						if (!latestTabState) {
							setIndexing(false);
							indexingRef.current = false;
							return;
						}

						// Trigger indexing with index=true
						const indexResponse = await fileService.writeFile(
							saveFilePath,
							latestTabState.tab.content,
							latestTabState.tab.encoding || "utf-8",
							latestTabState.tab.etag,
							true, // index=true: inject IDs
						);

						const indexedTabState = getTabForPath(saveFilePath);
						if (!indexedTabState) {
							setIndexing(false);
							indexingRef.current = false;
							return;
						}

						// Check for workflow ID conflicts
						if (
							indexResponse.workflow_id_conflicts &&
							indexResponse.workflow_id_conflicts.length > 0
						) {
							// Show conflict dialog - user must decide
							setIndexing(false);
							setPendingWorkflowConflict({
								conflicts: indexResponse.workflow_id_conflicts,
								filePath: saveFilePath,
								content: latestTabState.tab.content,
								encoding: latestTabState.tab.encoding || "utf-8",
								etag: latestTabState.tab.etag,
								tabIndex: indexedTabState.tabIndex,
							});
							indexingRef.current = false;
							return;
						}

						// Update editor with indexed content
						if (
							indexResponse.content_modified &&
							indexResponse.content
						) {
							updateTabContent(
								indexedTabState.tabIndex,
								indexResponse.content,
								indexResponse.etag,
							);
						}

						// Hide overlay
						setIndexing(false);
					} catch (error) {
						console.error("Failed to index file:", error);
						setIndexing(false);
					} finally {
						indexingRef.current = false;
					}
				}

				// Show green cloud for 2.5 seconds, but only transition if still "saved"
				setTimeout(() => {
					const latestTabState = getTabForPath(saveFilePath);
					if (latestTabState?.tab.saveState === "saved") {
						setSaveState(latestTabState.tabIndex, "clean");
					}
				}, 2500);

				// Run post-save tasks for Python workflow files
				if (isPythonFile) {
					// Incrementally reload workflows for this file
					// This updates the workflows store to reflect workflow changes
					void reloadWorkflowFile();
				}
			},
			(reason, conflictData) => {
				const currentTabState = getTabForPath(saveFilePath);
				if (!currentTabState) {
					return;
				}

				const { tabIndex } = currentTabState;

				// This runs when a conflict is detected
				if (
					reason === "workflows_would_deactivate" &&
					conflictData?.pending_deactivations
				) {
					// Show deactivation dialog instead of conflict state
					setPendingDeactivationConflict({
						pendingDeactivations:
							conflictData.pending_deactivations,
						availableReplacements:
							conflictData.available_replacements ?? [],
						filePath: saveFilePath,
						content: fileContent,
						encoding,
						etag: currentEtag,
						tabIndex,
					});
					// Reset save state to dirty (not conflict)
					setSaveState(tabIndex, "dirty");
				} else {
					setConflictState(tabIndex, reason);
				}
			},
			false, // index=false: detect only, don't inject
			() => {
				const latestTabState = getTabForPath(saveFilePath);
				if (latestTabState) {
					setSaveState(latestTabState.tabIndex, "dirty");
				}
			},
		);

		return () => clearTimeout(savingTimer);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [
		unsavedChanges,
		openFile,
		fileContent,
		encoding,
		currentEtag,
		activeTabIndex,
		enqueueSave,
		setSaveState,
		setConflictState,
	]);

	// Manual save function (for Cmd+S)
	// Uses same deferred indexing flow as auto-save
	const manualSave = useCallback(async () => {
		if (!openFile || !unsavedChanges) {
			return;
		}

		const isPythonFile = openFile.name.endsWith(".py");
		const saveFilePath = openFile.path;
		const initialTabState = getTabForPath(saveFilePath);
		if (initialTabState) {
			setSaveState(initialTabState.tabIndex, "saving");
		}

		try {
			// First save without indexing (fast path)
			const response = await fileService.writeFile(
				saveFilePath,
				fileContent,
				encoding,
				currentEtag,
				false, // index=false: detect only
			);

			const currentTabState = getTabForPath(saveFilePath);
			if (!currentTabState) {
				return;
			}

			const { tabIndex } = currentTabState;

			// Store diagnostics in tab state for CodeEditor to display
			setDiagnostics(
				tabIndex,
				response.diagnostics as FileDiagnostic[] | undefined,
			);

			// If server modified content, update editor buffer
			if (response.content_modified && response.content) {
				updateTabContent(tabIndex, response.content, response.etag);
			} else {
				// Update tab with new etag
				const state = useEditorStore.getState();
				const newTabs = [...state.tabs];
				if (newTabs[tabIndex]) {
					newTabs[tabIndex] = {
						...newTabs[tabIndex]!,
						etag: response.etag,
						unsavedChanges: false,
						saveState: "saved",
					};
					useEditorStore.setState({ tabs: newTabs });
				}
			}

			// Handle deferred indexing for Python files
			if (isPythonFile && response.needs_indexing) {
				setIndexing(true, "Indexing workflow...");

				try {
					const latestTabState = getTabForPath(saveFilePath);
					if (!latestTabState) {
						setIndexing(false);
						return;
					}

					// Trigger indexing with index=true
					const indexResponse = await fileService.writeFile(
						saveFilePath,
						latestTabState.tab.content,
						latestTabState.tab.encoding || "utf-8",
						latestTabState.tab.etag,
						true, // index=true: inject IDs
					);

					const indexedTabState = getTabForPath(saveFilePath);
					if (!indexedTabState) {
						setIndexing(false);
						return;
					}

					// Check for workflow ID conflicts
					if (
						indexResponse.workflow_id_conflicts &&
						indexResponse.workflow_id_conflicts.length > 0
					) {
						// Show conflict dialog - user must decide
						setIndexing(false);
						setPendingWorkflowConflict({
							conflicts: indexResponse.workflow_id_conflicts,
							filePath: saveFilePath,
							content: latestTabState.tab.content,
							encoding: latestTabState.tab.encoding || "utf-8",
							etag: latestTabState.tab.etag,
							tabIndex: indexedTabState.tabIndex,
						});
						return;
					}

					// Update editor with indexed content
					if (
						indexResponse.content_modified &&
						indexResponse.content
					) {
						updateTabContent(
							indexedTabState.tabIndex,
							indexResponse.content,
							indexResponse.etag,
						);
					}
				} catch (indexError) {
					console.error("Failed to index file:", indexError);
				} finally {
					setIndexing(false);
				}
			}

			// Show green cloud briefly, but only transition if still "saved"
			setTimeout(() => {
				const latestTabState = getTabForPath(saveFilePath);
				if (latestTabState?.tab.saveState === "saved") {
					setSaveState(latestTabState.tabIndex, "clean");
				}
			}, 2500);

			// Reload workflows if Python file
			if (isPythonFile) {
				void reloadWorkflowFile();
			}
		} catch (error) {
			if (error instanceof FileConflictError) {
				const reason = error.conflictData.reason as ConflictReason;
				const currentTabState = getTabForPath(saveFilePath);
				if (!currentTabState) {
					return;
				}

				const { tabIndex } = currentTabState;
				if (
					reason === "workflows_would_deactivate" &&
					error.conflictData.pending_deactivations
				) {
					// Show deactivation dialog instead of conflict state
					setPendingDeactivationConflict({
						pendingDeactivations:
							error.conflictData.pending_deactivations,
						availableReplacements:
							error.conflictData.available_replacements ?? [],
						filePath: saveFilePath,
						content: fileContent,
						encoding,
						etag: currentEtag,
						tabIndex,
					});
					// Reset save state to dirty (not conflict)
					setSaveState(tabIndex, "dirty");
				} else {
					// Show conflict state for other conflict types
					setConflictState(tabIndex, reason);
				}
			} else {
				console.error("Failed to save:", error);
				const latestTabState = getTabForPath(saveFilePath);
				if (latestTabState) {
					setSaveState(latestTabState.tabIndex, "dirty");
				}
			}
		}
	}, [
		openFile,
		fileContent,
		encoding,
		currentEtag,
		unsavedChanges,
		setSaveState,
		setConflictState,
		setDiagnostics,
		setIndexing,
		updateTabContent,
		reloadWorkflowFile,
		setPendingWorkflowConflict,
		setPendingDeactivationConflict,
	]);

	return { manualSave };
}
