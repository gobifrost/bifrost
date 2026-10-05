/**
 * App Code Editor Layout
 *
 * Complete editor layout for App Builder with:
 * - File tree sidebar (using modular FileTree)
 * - Code editor (Monaco)
 * - Live preview panel
 */

import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	FileTree,
	createAppCodeOperations,
	appCodeIconResolver,
	validateAppCodePath,
} from "@/components/file-tree";
import { AppCodeEditor } from "./AppCodeEditor";
import { useAppCodeEditor } from "./useAppCodeEditor";
import { useAppCodeUpdates, type LastUpdate } from "@/hooks/useAppCodeUpdates";
import { authFetch } from "@/lib/api-client";
import { BundledAppShell } from "@/components/jsx-app/BundledAppShell";
import { toast } from "sonner";
import {
	Save,
	Play,
	Code,
	PanelLeftClose,
	PanelLeft,
	AppWindow,
} from "lucide-react";
import { DependencyPanel } from "./DependencyPanel";
import type {
	FileNode,
	FileContent,
	EditorCallbacks,
} from "@/components/file-tree/types";

interface AppCodeEditorLayoutProps {
	/** Application UUID */
	appId: string;
	/** Owning Solution forwarded to the app preview. */
	solutionId?: string | null;
	/** Application name for display */
	appName?: string;
	/** Application slug for building base path */
	appSlug?: string;
	/** Callback when files are saved */
	onSave?: (path: string, source: string, compiled: string) => Promise<void>;
	/** When true, the app is solution-managed: disable all write controls
	 *  (Save, file create/rename/delete). The API rejects writes regardless;
	 *  this is the read-only affordance (criterion 6). */
	readOnly?: boolean;
}

type ViewMode = "code" | "app";
type SidebarTab = "files" | "packages";
type OpenFile = {
	path: string;
	name: string;
	source: string;
	compiled: string | null;
};

/**
 * App Code Editor Layout
 *
 * Provides a complete IDE-like experience for editing App Builder apps.
 */
export function AppCodeEditorLayout({
	appId,
	solutionId,
	appName = "App",
	appSlug,
	onSave,
	readOnly = false,
}: AppCodeEditorLayoutProps) {
	const location = useLocation();

	// Layout state
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const isDesktop = useMediaQuery("(min-width: 768px)");
	const [mobileToolsOpen, setMobileToolsOpen] = useState(true);
	const toolsVisible = isDesktop ? !sidebarCollapsed : mobileToolsOpen;
	const [viewMode, setViewMode] = useState<ViewMode>("code");
	const [sidebarTab, setSidebarTab] = useState<SidebarTab>("files");

	// Compute base path for the app preview
	// The editor is now at /apps/{slug}/edit/* so we need to extract that base
	const basePath = useMemo(() => {
		if (appSlug) {
			return `/apps/${appSlug}/edit`;
		}
		// Fallback: extract from current location
		// Pattern: /apps/{slug}/edit/...
		const match = location.pathname.match(/^(\/apps\/[^/]+\/edit)/);
		return match ? match[1] : "/";
	}, [appSlug, location.pathname]);

	// Get the current route within the app (for display)
	const currentAppRoute = useMemo(() => {
		const prefix = basePath;
		if (location.pathname.startsWith(prefix)) {
			const route = location.pathname.slice(prefix.length) || "/";
			return route;
		}
		return "/";
	}, [basePath, location.pathname]);

	// File state
	const [currentFile, setCurrentFile] = useState<OpenFile | null>(null);
	const [pendingOpenFile, setPendingOpenFile] = useState<OpenFile | null>(
		null,
	);

	// Create app code operations for the file tree. For a solution-managed
	// (read-only) app, wrap the mutating ops so create/rename/delete reject
	// with the locked message instead of round-tripping to a 409.
	const operations = useMemo(() => {
		const ops = createAppCodeOperations(appId);
		if (!readOnly) return ops;
		const denied = () => {
			throw new Error(
				"Solution-managed entities can only be managed by deployment methods.",
			);
		};
		return {
			...ops,
			write: denied,
			createFolder: denied,
			delete: denied,
			rename: denied,
		};
	}, [appId, readOnly]);

	// Track file tree refresh counter - increments to trigger refresh
	const [fileTreeRefresh, setFileTreeRefresh] = useState(0);

	// Use a ref to track current file path so the callback can access it
	const currentFilePathRef = useRef<string | null>(null);
	const currentFileCompiledRef = useRef<string | null>(null);
	useEffect(() => {
		currentFilePathRef.current = currentFile?.path ?? null;
		currentFileCompiledRef.current = currentFile?.compiled ?? null;
	}, [currentFile?.compiled, currentFile?.path]);

	// App code editor hook for managing source, compilation, etc.
	const {
		state: editorState,
		setSource,
		loadSource,
		setCompiled,
		save: triggerSave,
	} = useAppCodeEditor({
		initialSource: currentFile?.source ?? "",
		initialCompiled: currentFile?.compiled ?? undefined,
		compileDelay: 300,
		onSave: async (source, compiled) => {
			// Solution-managed apps are read-only — never write (the API 409s
			// regardless; short-circuit so autosave/Cmd+S don't even try).
			if (readOnly) return;
			if (!currentFile) return;

			// Save to API and get compiled code back
			const response = await authFetch(
				`/api/applications/${appId}/files/${encodeURIComponent(currentFile.path)}`,
				{
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ source }),
				},
			);

			if (!response.ok) {
				throw new Error(`Failed to save: ${response.statusText}`);
			}

			const data = await response.json();

			// Update compiled code from server response
			if (data.compiled) {
				setCompiled(data.compiled);
			}

			// Call external save handler if provided
			await onSave?.(currentFile.path, source, data.compiled ?? compiled);

			toast.success("File saved", { description: currentFile.name });
		},
	});

	// Handle real-time updates from WebSocket via callback
	const handleWebSocketUpdate = useCallback(
		(update: LastUpdate) => {
			const { action, path, userName } = update;

			// Show toast for external changes (not from this editor session)
			if (action === "create") {
				toast.info(`${userName} created ${path.split("/").pop()}`, {
					duration: 2000,
				});
				// Trigger file tree refresh
				setFileTreeRefresh((n) => n + 1);
			} else if (action === "delete") {
				toast.info(`${userName} deleted ${path.split("/").pop()}`, {
					duration: 2000,
				});
				// If the deleted file was open, close it
				if (currentFilePathRef.current === path) {
					setCurrentFile(null);
				}
				// Trigger file tree refresh
				setFileTreeRefresh((n) => n + 1);
			} else if (action === "update") {
				// If the updated file is currently open, show a toast with reload option
				if (currentFilePathRef.current === path) {
					toast.info(`${userName} updated this file`, {
						duration: 2000,
						action: {
							label: "Reload",
							onClick: async () => {
								// Re-fetch the file content
								try {
									const content = await operations.read(path);
									if (
										content &&
										currentFilePathRef.current === path
									) {
										const nextSource = content.content;
										setCurrentFile((prev) =>
											prev
												? {
														...prev,
														source: nextSource,
													}
												: null,
										);
										loadSource(
											nextSource,
											currentFileCompiledRef.current,
										);
									}
								} catch (error) {
									console.error(
										"[AppCodeEditorLayout] Failed to reload file:",
										error,
									);
								}
							},
						},
					});
				}
			}
		},
		[loadSource, operations],
	);

	// Real-time updates via WebSocket
	useAppCodeUpdates({
		appId,
		enabled: true,
		onUpdate: handleWebSocketUpdate,
	});

	// Handle file open from file tree
	const handleFileOpen = useCallback(
		(file: FileNode, content: FileContent) => {
			// Get compiled from metadata, ensuring it's a string or null
			const compiledValue = file.metadata?.compiled;
			const compiled =
				typeof compiledValue === "string" ? compiledValue : null;

			const nextFile = {
				path: file.path,
				name: file.name,
				source: content.content,
				compiled,
			};

			if (editorState.isCompiling) {
				toast.error("Save in progress", {
					description:
						"Wait for the current save to finish before switching files.",
				});
				return;
			}

			if (currentFile?.path === nextFile.path) {
				return;
			}

			if (editorState.hasUnsavedChanges) {
				setPendingOpenFile(nextFile);
				return;
			}

			setCurrentFile(nextFile);
			loadSource(nextFile.source, nextFile.compiled);
			setMobileToolsOpen(false);
			setViewMode("code");
		},
		[
			currentFile?.path,
			editorState.hasUnsavedChanges,
			editorState.isCompiling,
			loadSource,
		],
	);

	const confirmPendingFileOpen = useCallback(() => {
		if (!pendingOpenFile || editorState.isCompiling) return;
		setCurrentFile(pendingOpenFile);
		loadSource(pendingOpenFile.source, pendingOpenFile.compiled);
		setMobileToolsOpen(false);
		setViewMode("code");
		setPendingOpenFile(null);
	}, [editorState.isCompiling, loadSource, pendingOpenFile]);

	// Editor callbacks for file tree integration
	const editorCallbacks = useMemo<EditorCallbacks>(
		() => ({
			onFileOpen: handleFileOpen,
			onFileDeleted: (path: string) => {
				if (pendingOpenFile?.path === path) {
					setPendingOpenFile(null);
				}
				if (currentFile?.path === path) {
					setCurrentFile(null);
				}
			},
			onFileRenamed: (oldPath: string, newPath: string) => {
				if (currentFile?.path === oldPath) {
					setCurrentFile((prev) =>
						prev
							? {
									...prev,
									path: newPath,
									name: newPath.split("/").pop() || newPath,
								}
							: null,
					);
				}
			},
			isFileSelected: (path: string) => currentFile?.path === path,
		}),
		[currentFile, handleFileOpen, pendingOpenFile?.path],
	);

	// Handle manual save
	const handleSave = useCallback(async () => {
		if (readOnly) {
			toast.error(
				"This app is managed by a Solution and is read-only here.",
			);
			return;
		}
		if (!currentFile || editorState.errors.length > 0) {
			if (editorState.errors.length > 0) {
				toast.error("Cannot save with errors");
			}
			return;
		}

		await triggerSave();
	}, [readOnly, currentFile, editorState.errors, triggerSave]);

	// Handle run/preview
	const handleRun = useCallback(() => {
		setMobileToolsOpen(false);
		if (viewMode === "code") {
			setViewMode("app");
		}
	}, [viewMode]);

	return (
		<div className="h-full min-h-0 min-w-0 flex flex-col bg-background">
			{/* Toolbar */}
			<div className="flex shrink-0 flex-wrap items-center justify-between gap-x-2 px-2 border-b bg-muted/30 md:min-h-10">
				<div className="flex min-w-0 flex-1 items-center gap-2">
					{/* Sidebar toggle */}
					<Button
						variant="ghost"
						size="icon"
						className="size-11 md:size-8"
						onClick={() =>
							isDesktop
								? setSidebarCollapsed(!sidebarCollapsed)
								: setMobileToolsOpen(!mobileToolsOpen)
						}
						aria-label={
							toolsVisible
								? "Hide files and packages"
								: "Show files and packages"
						}
						aria-expanded={toolsVisible}
						title={
							toolsVisible
								? "Hide files and packages"
								: "Show files and packages"
						}
					>
						{!toolsVisible ? (
							<PanelLeft className="h-4 w-4" />
						) : (
							<PanelLeftClose className="h-4 w-4" />
						)}
					</Button>

					{/* File name */}
					<span className="min-w-0 truncate text-sm font-medium">
						{currentFile?.name || appName}
					</span>

					{/* Unsaved indicator */}
					{editorState.hasUnsavedChanges && (
						<span className="text-xs text-muted-foreground">
							(unsaved)
						</span>
					)}
				</div>

				<div className="flex w-full shrink-0 items-center justify-end gap-1 border-t md:w-auto md:border-0">
					{/* Current app route indicator (in app view) */}
					{viewMode === "app" && (
						<span className="min-w-0 truncate text-xs text-muted-foreground mr-auto font-mono md:max-w-40">
							{currentAppRoute}
						</span>
					)}

					{/* View mode toggles */}
					<div className="flex items-center gap-0.5 mr-2">
						<Button
							variant={
								viewMode === "code" ? "secondary" : "ghost"
							}
							size="icon"
							className="size-11 md:size-8"
							onClick={() => {
								setViewMode("code");
								setMobileToolsOpen(false);
							}}
							aria-label="Code"
							aria-pressed={
								viewMode === "code" &&
								(isDesktop || !mobileToolsOpen)
							}
							title="Code only"
						>
							<Code className="h-4 w-4" />
						</Button>
						<Button
							variant={viewMode === "app" ? "secondary" : "ghost"}
							size="icon"
							className="size-11 md:size-8"
							onClick={() => {
								setViewMode("app");
								setMobileToolsOpen(false);
							}}
							aria-label="App preview"
							aria-pressed={
								viewMode === "app" &&
								(isDesktop || !mobileToolsOpen)
							}
							title="Full app preview (with navigation)"
						>
							<AppWindow className="h-4 w-4" />
						</Button>
					</div>

					{/* Run button */}
					<Button
						variant="ghost"
						size="sm"
						onClick={handleRun}
						className="h-11 gap-1 md:h-8"
						title="Run preview (Cmd+Enter)"
					>
						<Play className="h-4 w-4" />
						Run
					</Button>

					{/* Save button */}
					<Button
						variant="ghost"
						size="sm"
						onClick={handleSave}
						disabled={
							readOnly ||
							!currentFile ||
							editorState.isCompiling ||
							!editorState.hasUnsavedChanges ||
							editorState.errors.length > 0
						}
						className="h-11 gap-1 md:h-8"
						title={
							readOnly
								? "Managed by a Solution — read-only"
								: "Save (Cmd+S)"
						}
					>
						<Save className="h-4 w-4" />
						{editorState.isCompiling
							? "Saving…"
							: editorState.saveError
								? "Retry save"
								: "Save"}
					</Button>
				</div>
			</div>

			{editorState.saveError && (
				<div
					role="alert"
					className="shrink-0 border-b border-[var(--bf-danger)]/30 bg-[var(--bf-danger-soft)] px-3 py-2 text-sm text-[var(--bf-danger)] [overflow-wrap:anywhere]"
				>
					Could not save. {editorState.saveError} Your changes are
					still in the editor.
				</div>
			)}

			<AlertDialog
				open={pendingOpenFile !== null}
				onOpenChange={(open) => {
					if (!open) {
						setPendingOpenFile(null);
					}
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Discard unsaved changes?
						</AlertDialogTitle>
						<AlertDialogDescription className="[overflow-wrap:anywhere]">
							{pendingOpenFile
								? `Open ${pendingOpenFile.name}? Your unsaved changes to ${currentFile?.name || "the current file"} will be lost.`
								: "Open this file? Your unsaved changes will be lost."}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel className="min-h-11">
							Keep editing
						</AlertDialogCancel>
						<AlertDialogAction
							className="min-h-11"
							variant="destructive"
							disabled={editorState.isCompiling}
							onClick={(event) => {
								event.preventDefault();
								confirmPendingFileOpen();
							}}
						>
							Discard changes
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Main content */}
			<div className="flex-1 min-h-0 flex">
				{/* Sidebar */}
				<div
					hidden={!toolsVisible}
					className={cn(
						"min-h-0 w-full border-r shrink-0 flex-col md:w-60",
						toolsVisible ? "flex" : "hidden",
					)}
				>
					{/* Tab switcher */}
					<div className="flex border-b">
						<button
							className={`flex-1 min-h-11 px-3 py-1.5 text-xs font-medium md:min-h-9 ${
								sidebarTab === "files"
									? "border-b-2 border-primary text-foreground"
									: "text-muted-foreground hover:text-foreground"
							}`}
							aria-pressed={sidebarTab === "files"}
							onClick={() => setSidebarTab("files")}
						>
							Files
						</button>
						<button
							className={`flex-1 min-h-11 px-3 py-1.5 text-xs font-medium md:min-h-9 ${
								sidebarTab === "packages"
									? "border-b-2 border-primary text-foreground"
									: "text-muted-foreground hover:text-foreground"
							}`}
							aria-pressed={sidebarTab === "packages"}
							onClick={() => setSidebarTab("packages")}
						>
							Packages
						</button>
					</div>

					{/* Tab content */}
					{sidebarTab === "files" ? (
						<div className="flex-1 overflow-auto">
							<FileTree
								operations={operations}
								iconResolver={appCodeIconResolver}
								editor={editorCallbacks}
								refreshTrigger={fileTreeRefresh}
								config={{
									enableUpload: false,
									enableDragMove: !readOnly,
									enableCreate: !readOnly,
									enableRename: !readOnly,
									enableDelete: !readOnly,
									emptyMessage: "No files yet",
									loadingMessage: "Loading files...",
									pathValidator: validateAppCodePath,
								}}
							/>
						</div>
					) : (
						<DependencyPanel
							key={appId}
							appId={appId}
							readOnly={readOnly}
						/>
					)}
				</div>

				{/* Editor and Preview */}
				<div
					hidden={!isDesktop && mobileToolsOpen}
					className={cn(
						"flex-1 min-w-0 min-h-0",
						!isDesktop && mobileToolsOpen ? "hidden" : "flex",
					)}
				>
					<div
						hidden={viewMode !== "code"}
						className={cn(
							"flex-1 min-w-0",
							viewMode !== "code" && "hidden",
						)}
					>
						{currentFile ? (
							<AppCodeEditor
								value={editorState.source}
								onChange={setSource}
								onSave={handleSave}
								errors={editorState.errors}
								path={currentFile.path}
								readOnly={readOnly}
							/>
						) : (
							<div className="h-full flex items-center justify-center text-muted-foreground">
								<p className="text-sm">Select a file to edit</p>
							</div>
						)}
					</div>
					{viewMode === "app" && (
						/* App preview - full app with navigation */
						<div className="flex-1 min-h-0 overflow-hidden">
							<BundledAppShell
								appId={appId}
								solutionId={solutionId}
								appSlug={appSlug || ""}
								isPreview={true}
							/>
						</div>
					)}
				</div>
			</div>

			{/* Status bar */}
			<div className="flex shrink-0 items-center justify-between gap-3 min-h-7 px-2 border-t bg-muted/30 text-xs text-muted-foreground">
				<div className="flex min-w-0 items-center gap-4">
					{currentFile && (
						<span
							className="truncate font-mono"
							title={currentFile.path}
						>
							{currentFile.path}
						</span>
					)}
				</div>
				<div className="flex min-w-0 items-center gap-4">
					{editorState.errors.length > 0 && (
						<span className="text-destructive">
							{editorState.errors.length} error
							{editorState.errors.length > 1 ? "s" : ""}
						</span>
					)}
					{editorState.isCompiling && (
						<span className="text-[var(--bf-warning)]">
							Compiling...
						</span>
					)}
				</div>
			</div>
		</div>
	);
}
