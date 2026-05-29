import { useEffect, useRef, useCallback, useState } from "react";
import Editor, { type OnMount, type BeforeMount } from "@monaco-editor/react";
import { useEditorStore } from "@/stores/editorStore";
import { useEditorSession } from "@/hooks/useEditorSession";
import { useAutoSave } from "@/hooks/useAutoSave";
import { useWindowFocusRefresh } from "@/hooks/useWindowFocusRefresh";
import { useTheme } from "@/contexts/ThemeContext";
import { useCmdCtrlShortcut } from "@/contexts/KeyboardContext";
import { fileService } from "@/services/fileService";
import type { FileDiagnostic } from "@/stores/editorStore";
import { Loader2, FileIcon } from "lucide-react";
import { toast } from "sonner";
import type * as Monaco from "monaco-editor";
import { initializeMonaco, setCurrentFilePath } from "@/lib/monaco-setup";
import { registerWorkflow } from "@/hooks/useWorkflows";
import { useReloadWorkflowFile } from "@/hooks/useWorkflows";
import { ConflictDiffView } from "./ConflictDiffView";
import { SyncDiffView } from "./SyncDiffView";
import { IndexingOverlay } from "./IndexingOverlay";
import { WorkflowIdConflictDialog } from "./WorkflowIdConflictDialog";
import { WorkflowDeactivationDialog } from "./WorkflowDeactivationDialog";
import { RegisterWorkflowDialog } from "./RegisterWorkflowDialog";

/**
 * Monaco editor component wrapper
 * Provides code editing with syntax highlighting, auto-save, and manual save
 */
export function CodeEditor() {
	const {
		openFile,
		fileContent,
		fileEncoding,
		unsavedChanges,
		gitConflict,
		isLoadingFile,
		diagnostics,
		setFileContent,
		setCursorPosition,
		setSelectedLanguage,
		markSaved,
		setConflictState,
	} = useEditorSession();

	useAutoSave(); // Still use auto-save for debounced saving
	useWindowFocusRefresh(); // Still refresh file tree on window focus
	const { theme } = useTheme();
	const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
	const monacoRef = useRef<typeof Monaco | null>(null);
	const conflictDisposableRef = useRef<Monaco.IDisposable | null>(null);

	// Indexing state for blocking overlay during ID injection
	const isIndexing = useEditorStore((state) => state.isIndexing);

	// Diff preview state for sync UI
	const diffPreview = useEditorStore((state) => state.diffPreview);

	// Workflow ID conflict state
	const pendingWorkflowConflict = useEditorStore(
		(state) => state.pendingWorkflowConflict,
	);
	const resolveWorkflowIdConflict = useEditorStore(
		(state) => state.resolveWorkflowIdConflict,
	);

	// Deactivation conflict state
	const pendingDeactivationConflict = useEditorStore(
		(state) => state.pendingDeactivationConflict,
	);
	const resolveDeactivationConflict = useEditorStore(
		(state) => state.resolveDeactivationConflict,
	);

	const updateTabContent = useEditorStore((state) => state.updateTabContent);
	const setDiagnostics = useEditorStore((state) => state.setDiagnostics);
	const activeTabIndex = useEditorStore((state) => state.activeTabIndex);

	// Line reveal state (for scrolling to specific line when opening from maintenance page, etc.)
	const pendingLineReveal = useEditorStore(
		(state) => state.pendingLineReveal,
	);
	const clearPendingLineReveal = useEditorStore(
		(state) => state.clearPendingLineReveal,
	);

	// Workflow registration support for CodeLens
	const reloadWorkflows = useReloadWorkflowFile();

	// Register dialog state
	const [registerDialog, setRegisterDialog] = useState<{
		open: boolean;
		filePath: string;
		functionName: string;
	}>({ open: false, filePath: "", functionName: "" });

	// Keep Monaco CodeLens provider aware of the current file path
	useEffect(() => {
		setCurrentFilePath(openFile?.path ?? null);
		return () => setCurrentFilePath(null);
	}, [openFile?.path]);

	// Listen for CodeLens "Register" button clicks — open dialog instead of registering directly
	useEffect(() => {
		const handler = (e: Event) => {
			const { filePath, functionName } = (e as CustomEvent).detail as {
				filePath: string;
				functionName: string;
			};
			setRegisterDialog({ open: true, filePath, functionName });
		};
		window.addEventListener("bifrost-register-decorator", handler);
		return () =>
			window.removeEventListener("bifrost-register-decorator", handler);
	}, []);

	// Handle confirmed registration from dialog
	const handleRegisterConfirm = useCallback(
		async (orgId: string | null) => {
			const { filePath, functionName } = registerDialog;
			setRegisterDialog({ open: false, filePath: "", functionName: "" });
			try {
				const result = await registerWorkflow(filePath, functionName, orgId);
				const orgLabel = result.organization_id ? "" : " (Global)";
				toast.success(`Registered ${functionName}${orgLabel}`);
				// Refresh workflow store so CodeLens updates
				await reloadWorkflows.mutate();
				// Force CodeLens to re-evaluate by toggling language
				const editor = editorRef.current;
				const monaco = monacoRef.current;
				if (editor && monaco) {
					const model = editor.getModel();
					if (model) {
						monaco.editor.setModelLanguage(model, "plaintext");
						monaco.editor.setModelLanguage(model, "python");
					}
				}
			} catch (err) {
				toast.error("Failed to register", {
					description:
						err instanceof Error ? err.message : String(err),
				});
			}
		},
		[registerDialog, reloadWorkflows],
	);

	// Check for conflicts by comparing etags
	const checkForConflict = useCallback(async () => {
		// Get fresh state from store (not from closure)
		const state = useEditorStore.getState();
		const freshTab = state.tabs[state.activeTabIndex];

		if (
			!freshTab ||
			!freshTab.file ||
			!freshTab.etag ||
			freshTab.saveState === "conflict" ||
			freshTab.saveState === "saving" ||
			state.isIndexing
		) {
			return; // Skip if no file, no etag, already in conflict, saving, or indexing
		}

		try {
			const serverFile = await fileService.readFile(freshTab.file.path);

			// Check if server content changed
			if (serverFile.etag !== freshTab.etag) {
				// If there are no unsaved changes, just accept the server version silently
				if (!freshTab.unsavedChanges) {
					const newTabs = [...state.tabs];
					newTabs[state.activeTabIndex] = {
						...freshTab,
						content: serverFile.content,
						etag: serverFile.etag,
						encoding: serverFile.encoding as "utf-8" | "base64",
					};
					useEditorStore.setState({ tabs: newTabs });
				} else {
					// Only show conflict if we have unsaved local changes
					setConflictState(state.activeTabIndex, "content_changed");
				}
			}
		} catch (error: unknown) {
			// Check if file was deleted (404)
			if (error instanceof Error && error.message.includes("404")) {
				setConflictState(state.activeTabIndex, "path_not_found");
			}
			// Ignore other errors (network issues, etc.)
		}
	}, [setConflictState]);

	// Manual save handler
	const handleManualSave = useCallback(async () => {
		if (!openFile || !unsavedChanges) {
			return;
		}

		try {
			// Format document before saving
			if (editorRef.current) {
				await editorRef.current
					.getAction("editor.action.formatDocument")
					?.run();
			}

			const response = await fileService.writeFile(
				openFile.path,
				fileContent,
			);

			// Store diagnostics in tab state - the useEffect watching diagnostics will apply to Monaco
			setDiagnostics(
				activeTabIndex,
				response.diagnostics as FileDiagnostic[] | undefined,
			);

			markSaved();

			toast.success("File saved", {
				description: openFile.name,
			});
		} catch (error) {
			toast.error("Failed to save file", {
				description:
					error instanceof Error ? error.message : String(error),
			});
		}
	}, [
		openFile,
		fileContent,
		unsavedChanges,
		markSaved,
		setDiagnostics,
		activeTabIndex,
	]);

	// Register global Cmd/Ctrl+S shortcut for saving
	useCmdCtrlShortcut("editor-save", "s", () => {
		handleManualSave();
	});

	// Handle Cmd+Enter keydown on the editor container (MUST be before any early returns)
	const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
		// Check for Cmd+Enter (Mac) or Ctrl+Enter (Windows/Linux)
		if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
			e.preventDefault();
			e.stopPropagation();

			// Trigger the run-editor-file event
			const event = new CustomEvent("run-editor-file");
			window.dispatchEvent(event);
		}
	}, []);

	// Determine Monaco theme based on app theme
	const monacoTheme = theme === "light" ? "vs" : "vs-dark";

	// Detect language from file extension
	const getLanguage = (filename: string): string => {
		const ext = filename.split(".").pop()?.toLowerCase();
		const languageMap: Record<string, string> = {
			js: "javascript",
			jsx: "javascript",
			ts: "typescript",
			tsx: "typescript",
			py: "python",
			json: "json",
			yaml: "yaml",
			yml: "yaml",
			md: "markdown",
			html: "html",
			css: "css",
			scss: "scss",
			sql: "sql",
			sh: "shell",
			xml: "xml",
			txt: "plaintext",
		};
		return languageMap[ext || ""] || "plaintext";
	};

	// Configure Monaco BEFORE it mounts - this is critical for comment support
	const handleEditorWillMount: BeforeMount = async (monaco) => {
		await initializeMonaco(monaco);
	};

	const handleEditorMount: OnMount = async (editor, monaco) => {
		editorRef.current = editor;
		monacoRef.current = monaco;

		// Check for pending line reveal and execute it
		// This handles the case when opening a new file with a line number
		const pendingLine = useEditorStore.getState().pendingLineReveal;
		if (pendingLine) {
			// Small delay to ensure content is fully rendered
			setTimeout(() => {
				editor.revealLineInCenter(pendingLine);
				editor.setPosition({ lineNumber: pendingLine, column: 1 });
				editor.focus();
				useEditorStore.getState().clearPendingLineReveal();
			}, 50);
		}

		// Track cursor position
		editor.onDidChangeCursorPosition((e) => {
			setCursorPosition({
				line: e.position.lineNumber,
				column: e.position.column,
			});
		});

		// Check for conflicts when editor gains focus
		editor.onDidFocusEditorText(() => {
			checkForConflict();
		});

		// Listen for Cmd+Enter or Cmd+Shift+Enter using Monaco's onKeyDown event
		editor.onKeyDown((e) => {
			// Check for Cmd+Enter or Cmd+Shift+Enter (Mac) or Ctrl variants (Windows/Linux)
			const isCtrlOrCmd = e.ctrlKey || e.metaKey;
			const isEnter = e.keyCode === monaco.KeyCode.Enter;

			if (isCtrlOrCmd && isEnter) {
				e.preventDefault();
				e.stopPropagation();

				// Trigger the run-editor-file event
				const event = new CustomEvent("run-editor-file");

				window.dispatchEvent(event);
			}
		});

		// We no longer need to apply conflict decorations since markers aren't written to files
		// The conflict resolution is handled via the banner component
	};

	const handleEditorChange = (value: string | undefined) => {
		if (value !== undefined) {
			setFileContent(value);
		}
	};

	// Update language when file changes
	useEffect(() => {
		if (openFile) {
			const language = getLanguage(openFile.name);
			setSelectedLanguage(language);
		}
	}, [openFile, setSelectedLanguage]);

	// Apply diagnostics from store to Monaco markers
	// This watches the diagnostics in the store (set by useAutoSave after save)
	// and applies them as Monaco editor markers
	useEffect(() => {
		const editor = editorRef.current;
		const monaco = monacoRef.current;

		if (!editor || !monaco) return;

		const model = editor.getModel();
		if (!model) return;

		// Clear previous markers first
		monaco.editor.setModelMarkers(model, "bifrost", []);

		// If no diagnostics, we're done (markers cleared)
		if (!diagnostics || diagnostics.length === 0) return;

		// Convert diagnostics to Monaco markers
		const markers: Monaco.editor.IMarkerData[] = diagnostics.map((d) => ({
			severity:
				d.severity === "error"
					? monaco.MarkerSeverity.Error
					: d.severity === "warning"
						? monaco.MarkerSeverity.Warning
						: monaco.MarkerSeverity.Info,
			message: d.message,
			startLineNumber: d.line ?? 1,
			startColumn: d.column ?? 1,
			endLineNumber: d.line ?? 1,
			endColumn: 1000, // Highlight to end of line
			source: d.source ?? "bifrost",
		}));

		monaco.editor.setModelMarkers(model, "bifrost", markers);

		// Show toast with summary
		const errorCount = diagnostics.filter(
			(d) => d.severity === "error",
		).length;
		const warningCount = diagnostics.filter(
			(d) => d.severity === "warning",
		).length;

		if (errorCount > 0) {
			toast.warning(
				`${errorCount} error${errorCount > 1 ? "s" : ""} found`,
				{
					description:
						warningCount > 0
							? `Plus ${warningCount} warning${warningCount > 1 ? "s" : ""}`
							: undefined,
				},
			);
		} else if (warningCount > 0) {
			toast.info(
				`${warningCount} warning${warningCount > 1 ? "s" : ""} found`,
			);
		}
	}, [diagnostics]);

	// Cleanup conflict resolution when component unmounts or file changes
	useEffect(() => {
		return () => {
			if (conflictDisposableRef.current) {
				conflictDisposableRef.current.dispose();
				conflictDisposableRef.current = null;
			}
		};
	}, [openFile?.path]);

	// Handle pending line reveal (scroll to specific line after file loads)
	useEffect(() => {
		if (
			!editorRef.current ||
			!pendingLineReveal ||
			isLoadingFile ||
			!openFile
		) {
			return;
		}

		// Small delay to ensure editor content is fully rendered
		const timeoutId = setTimeout(() => {
			if (editorRef.current) {
				editorRef.current.revealLineInCenter(pendingLineReveal);
				editorRef.current.setPosition({
					lineNumber: pendingLineReveal,
					column: 1,
				});
				editorRef.current.focus();
			}
			clearPendingLineReveal();
		}, 100);

		return () => clearTimeout(timeoutId);
	}, [pendingLineReveal, isLoadingFile, openFile, clearPendingLineReveal]);

	if (isLoadingFile) {
		return (
			<div className="flex h-full items-center justify-center">
				<div className="text-center">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground mx-auto mb-2" />
					<p className="text-sm text-muted-foreground">
						Loading file...
					</p>
				</div>
			</div>
		);
	}

	// Show diff preview when active (for sync UI) - must check BEFORE !openFile
	// since sync diffs are viewed without opening a file
	if (diffPreview) {
		return <SyncDiffView preview={diffPreview} />;
	}

	if (!openFile) {
		return (
			<div className="flex h-full items-center justify-center">
				<div className="text-center">
					<p className="text-muted-foreground">No file open</p>
					<p className="mt-2 text-xs text-muted-foreground">
						Select a file from the file tree to start editing
					</p>
				</div>
			</div>
		);
	}

	// Check if file is binary (base64 encoded)
	if (fileEncoding === "base64") {
		const isImage = openFile.name.match(
			/\.(png|jpg|jpeg|gif|svg|webp|bmp|ico)$/i,
		);

		if (isImage) {
			// Show image preview
			return (
				<div className="flex h-full items-center justify-center bg-muted/30 p-4">
					<div className="flex flex-col items-center gap-4 max-w-full max-h-full">
						<img
							src={`data:image/${openFile.name
								.split(".")
								.pop()
								?.toLowerCase()};base64,${fileContent}`}
							alt={openFile.name}
							className="max-w-full max-h-[calc(100vh-200px)] object-contain rounded-lg shadow-lg"
						/>
						<div className="text-center">
							<p className="text-sm font-medium">
								{openFile.name}
							</p>
							<p className="text-xs text-muted-foreground mt-1">
								{openFile.size
									? `${(openFile.size / 1024).toFixed(1)} KB`
									: "Unknown size"}
							</p>
						</div>
					</div>
				</div>
			);
		} else {
			// Show binary file placeholder
			return (
				<div className="flex h-full items-center justify-center">
					<div className="text-center">
						<FileIcon className="h-16 w-16 mx-auto mb-4 text-muted-foreground" />
						<p className="text-lg font-medium">{openFile.name}</p>
						<p className="text-sm text-muted-foreground mt-2">
							Unable to preview binary file
						</p>
						<p className="text-xs text-muted-foreground mt-1">
							{openFile.size
								? `Size: ${(openFile.size / 1024).toFixed(
										1,
									)} KB`
								: "Unknown size"}
						</p>
					</div>
				</div>
			);
		}
	}

	const language = getLanguage(openFile.name);
	const fileSize = openFile.size || 0;
	const isLargeFile = fileSize > 5_000_000; // 5MB

	const handleConflictResolve = async (choice: "current" | "incoming") => {
		if (!openFile || !gitConflict) return;

		try {
			// Choose the appropriate content based on user selection
			const resolvedContent =
				choice === "current"
					? gitConflict.current_content
					: gitConflict.incoming_content;

			// Save resolved content
			await fileService.writeFile(openFile.path, resolvedContent);

			// Update editor state
			setFileContent(resolvedContent);

			// Clear conflicts from the current tab
			const state = useEditorStore.getState();
			const newTabs = [...state.tabs];
			const currentTab = newTabs[state.activeTabIndex];
			if (currentTab) {
				// eslint-disable-next-line @typescript-eslint/no-unused-vars
				const { gitConflict, ...rest } = currentTab;
				newTabs[state.activeTabIndex] = rest;
			}
			useEditorStore.setState({ tabs: newTabs });

			toast.success("Conflict resolved");

			// Trigger a custom event to refresh source control status
			window.dispatchEvent(new CustomEvent("git-status-changed"));
		} catch (error) {
			toast.error("Failed to resolve conflict", {
				description:
					error instanceof Error ? error.message : String(error),
			});
		}
	};

	// If there's a conflict, show the diff view instead of the regular editor
	if (gitConflict) {
		return (
			<ConflictDiffView
				conflict={gitConflict}
				filePath={openFile?.path || ""}
				onResolve={handleConflictResolve}
			/>
		);
	}

	return (
		<div className="h-full w-full flex flex-col" onKeyDown={handleKeyDown}>
			<div className="flex-1 relative">
				<Editor
					height="100%"
					language={language}
					value={fileContent}
					onChange={handleEditorChange}
					beforeMount={handleEditorWillMount}
					onMount={handleEditorMount}
					theme={monacoTheme}
					options={{
						// Display
						minimap: { enabled: !isLargeFile },
						scrollBeyondLastLine: false,
						fontSize: 14,
						wordWrap: "on",
						automaticLayout: true,
						renderWhitespace: "selection",
						cursorBlinking: "smooth",
						smoothScrolling: true,

						// Indentation
						tabSize: 4,
						insertSpaces: true,

						// Formatting
						formatOnPaste: true,
						formatOnType: true,

						// Context menu
						contextmenu: true,

						// Auto-closing
						autoClosingBrackets: "always",
						autoClosingQuotes: "always",
						autoSurround: "languageDefined",

						// Bracket colorization
						bracketPairColorization: {
							enabled: true,
						},

						// Code folding
						showFoldingControls: "always",
						foldingStrategy: "indentation",

						// IntelliSense
						quickSuggestions: {
							other: true,
							comments: false,
							strings: true,
						},
						suggestOnTriggerCharacters: true,
						acceptSuggestionOnCommitCharacter: true,
						acceptSuggestionOnEnter: "on",

						// Multi-cursor
						multiCursorModifier: "ctrlCmd",

						// Find widget
						find: {
							seedSearchStringFromSelection: "selection",
							autoFindInSelection: "never",
						},

						// Code lens (for conflict resolution buttons)
						codeLens: true,

						readOnly: isIndexing,
					}}
					loading={
						<div className="flex h-full items-center justify-center">
							<div className="text-sm text-muted-foreground">
								Loading editor...
							</div>
						</div>
					}
				/>
				{/* Indexing overlay blocks editor during ID injection */}
				<IndexingOverlay />

				{/* Workflow ID conflict dialog */}
				<WorkflowIdConflictDialog
					conflicts={pendingWorkflowConflict?.conflicts ?? []}
					open={pendingWorkflowConflict !== null}
					onUseExisting={async () => {
						const response =
							await resolveWorkflowIdConflict("use_existing");
						if (response && pendingWorkflowConflict) {
							// Update tab with the new content (IDs injected)
							if (response.content_modified && response.content) {
								updateTabContent(
									pendingWorkflowConflict.tabIndex,
									response.content,
									response.etag,
								);
							}
							toast.success("Existing workflow IDs preserved");
						}
					}}
					onGenerateNew={async () => {
						const response =
							await resolveWorkflowIdConflict("generate_new");
						if (response && pendingWorkflowConflict) {
							// Update tab with the new content (new IDs generated)
							if (response.content_modified && response.content) {
								updateTabContent(
									pendingWorkflowConflict.tabIndex,
									response.content,
									response.etag,
								);
							}
							toast.info("New workflow IDs generated");
						}
					}}
					onCancel={() => {
						resolveWorkflowIdConflict("cancel");
					}}
				/>

				{/* Register workflow dialog */}
				<RegisterWorkflowDialog
					open={registerDialog.open}
					functionName={registerDialog.functionName}
					onConfirm={handleRegisterConfirm}
					onCancel={() =>
						setRegisterDialog({ open: false, filePath: "", functionName: "" })
					}
				/>

				{/* Workflow Deactivation dialog */}
				<WorkflowDeactivationDialog
					pendingDeactivations={
						pendingDeactivationConflict?.pendingDeactivations ?? []
					}
					availableReplacements={
						pendingDeactivationConflict?.availableReplacements ?? []
					}
					open={pendingDeactivationConflict !== null}
					onResolve={async (replacements, workflowsToDeactivate) => {
						const response = await resolveDeactivationConflict(
							"apply",
							Object.keys(replacements).length > 0 ? replacements : undefined,
							workflowsToDeactivate.length > 0 ? workflowsToDeactivate : undefined,
						);
						if (response && pendingDeactivationConflict) {
							if (response.content_modified && response.content) {
								updateTabContent(
									pendingDeactivationConflict.tabIndex,
									response.content,
									response.etag,
								);
							} else {
								updateTabContent(
									pendingDeactivationConflict.tabIndex,
									pendingDeactivationConflict.content,
									response.etag,
								);
							}
							const mapped = Object.keys(replacements).length;
							const deactivated = workflowsToDeactivate.length;
							if (mapped > 0 && deactivated > 0) {
								toast.success(`${mapped} transferred, ${deactivated} deactivated`);
							} else if (mapped > 0) {
								toast.success("Workflow identities transferred");
							} else {
								toast.info("Workflows deactivated");
							}
						}
					}}
					onCancel={() => {
						resolveDeactivationConflict("cancel");
					}}
				/>
			</div>
		</div>
	);
}
