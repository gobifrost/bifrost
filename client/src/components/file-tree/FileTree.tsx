/**
 * Modular File Tree Component
 *
 * A reusable file tree component that works with different backends:
 * - Workspace files (code editor)
 * - JSX files (app builder)
 * - Organization-scoped file trees
 *
 * Dependencies are injected rather than hardcoded, making the component
 * portable across different contexts.
 */

import { useEffect, useCallback, useState, useRef, useMemo, memo } from "react";
import {
	ChevronRight,
	ChevronDown,
	File,
	Folder,
	FilePlus,
	FolderPlus,
	Trash2,
	Edit2,
	RefreshCw,
	Loader2,
	Building2,
	Lock,
	MoreHorizontal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuSeparator,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";
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

import { FileTreeReadError } from "./FileTreeReadError";
import { useFileTree } from "./useFileTree";
import { defaultIconResolver } from "./icons";
import type {
	FileNode,
	FileTreeNode,
	FileTreeProps,
	FileIconResolver,
	EditorCallbacks,
	FileTreeConfig,
	FileOperations,
	PathValidator,
} from "./types";

type CreatingItemType = "file" | "folder" | null;

/**
 * Resolved config type with pathValidator always present (but possibly undefined)
 */
type ResolvedConfig = Omit<Required<FileTreeConfig>, "pathValidator"> & {
	pathValidator: PathValidator | undefined;
};

/**
 * Default configuration for file tree behavior
 */
const DEFAULT_CONFIG: ResolvedConfig = {
	enableUpload: false, // Disabled by default - workspace adapter enables it
	enableDragMove: true,
	enableCreate: true,
	enableRename: true,
	enableDelete: true,
	emptyMessage: "No files found",
	loadingMessage: "Loading files...",
	pathValidator: undefined,
};

/**
 * Modular File Tree Component
 *
 * @param operations - File operations implementation (required)
 * @param editor - Optional editor integration callbacks
 * @param iconResolver - Optional custom icon resolver
 * @param config - Optional behavior configuration
 * @param className - Optional additional CSS classes
 */
export function FileTree({
	operations,
	editor,
	iconResolver = defaultIconResolver,
	config: userConfig,
	className,
	refreshTrigger,
	onChangeScope,
}: FileTreeProps) {
	// Memoize config to avoid recreating on every render
	const config: ResolvedConfig = useMemo(
		() => ({ ...DEFAULT_CONFIG, ...userConfig }),
		[userConfig],
	);

	const {
		files,
		failedPaths,
		isLoading,
		isFolderLoading,
		loadFiles,
		toggleFolder,
		isFolderExpanded,
		refreshAll,
		removeFromTree,
	} = useFileTree(operations);

	const [creatingItem, setCreatingItem] = useState<CreatingItemType>(null);
	const [newItemName, setNewItemName] = useState("");
	const [creatingInFolder, setCreatingInFolder] = useState<string | null>(
		null,
	);
	const [renamingFile, setRenamingFile] = useState<FileNode | null>(null);
	const [renameValue, setRenameValue] = useState("");
	const [fileToDelete, setFileToDelete] = useState<FileNode | null>(null);
	const [dragOverFolder, setDragOverFolder] = useState<string | null>(null);
	const [isProcessing, setIsProcessing] = useState(false);

	const inputRef = useRef<HTMLInputElement>(null);
	const renameInputRef = useRef<HTMLInputElement>(null);

	// Ref for stable access to files without including in dependency arrays
	const filesRef = useRef(files);
	useEffect(() => {
		filesRef.current = files;
	}, [files]);

	// Load root directory on mount
	useEffect(() => {
		loadFiles("");
	}, [loadFiles]);

	// Refresh when refreshTrigger changes (for external refresh requests)
	const prevRefreshTrigger = useRef(refreshTrigger);
	useEffect(() => {
		if (
			refreshTrigger !== undefined &&
			prevRefreshTrigger.current !== undefined &&
			refreshTrigger !== prevRefreshTrigger.current
		) {
			refreshAll();
		}
		prevRefreshTrigger.current = refreshTrigger;
	}, [refreshTrigger, refreshAll]);

	// Notify editor of loading state changes
	useEffect(() => {
		editor?.onLoadingChange?.(isLoading);
	}, [isLoading, editor]);

	const handleFileClick = useCallback(
		async (file: FileNode) => {
			if (file.type === "folder") {
				toggleFolder(file.path);
				return;
			}

			// Auto-expand parent folders
			try {
				const pathParts = file.path.split("/");
				pathParts.pop(); // Remove filename
				let currentPath = "";
				for (const part of pathParts) {
					currentPath = currentPath ? `${currentPath}/${part}` : part;
					if (!isFolderExpanded(currentPath)) {
						await toggleFolder(currentPath);
					}
				}
			} catch {
				// Ignore folder expansion errors
			}

			// Read file and notify editor
			if (editor?.onFileOpen) {
				try {
					const content = await operations.read(file.path);
					editor.onFileOpen(file, content);
				} catch (err) {
					toast.error("Failed to open file", {
						description:
							err instanceof Error ? err.message : String(err),
					});
				}
			}
		},
		[toggleFolder, isFolderExpanded, editor, operations],
	);

	const handleFolderToggle = useCallback(
		(folder: FileNode) => {
			void toggleFolder(folder.path);
		},
		[toggleFolder],
	);

	const handleCancelNewItem = useCallback(() => {
		setCreatingItem(null);
		setNewItemName("");
		setCreatingInFolder(null);
	}, []);

	const handleCreateFile = useCallback((folderPath?: string) => {
		setCreatingItem("file");
		setNewItemName("");
		setCreatingInFolder(folderPath || null);
	}, []);

	const handleCreateFolder = useCallback((folderPath?: string) => {
		setCreatingItem("folder");
		setNewItemName("");
		setCreatingInFolder(folderPath || null);
	}, []);

	const handleInputMouseDown = useCallback((e: React.MouseEvent) => {
		e.stopPropagation();
	}, []);

	const handleRefresh = useCallback(async () => {
		await refreshAll();
	}, [refreshAll]);

	const handleSaveNewItem = useCallback(async () => {
		if (!newItemName.trim() || !creatingItem) return;

		const fullPath = creatingInFolder
			? `${creatingInFolder}/${newItemName}`
			: newItemName;

		// Validate path if validator is configured
		if (config.pathValidator) {
			const validation = config.pathValidator(fullPath);
			if (!validation.valid) {
				toast.error(`Invalid ${creatingItem} path`, {
					description: validation.error,
				});
				return;
			}
		}

		try {
			setIsProcessing(true);

			if (creatingItem === "file") {
				await operations.write(fullPath, "");
			} else {
				await operations.createFolder(fullPath);
			}

			// Reload the parent folder to show the new item
			let parentPath = creatingInFolder || "";

			// If creating at root level and first item is an org container,
			// reload that container instead (org-scoped file tree)
			// Use filesRef.current for stable access without triggering callback recreation
			if (
				!creatingInFolder &&
				filesRef.current.length > 0 &&
				filesRef.current[0].path.startsWith("org:")
			) {
				// Find the first expanded org container, or default to first one
				const expandedOrg = filesRef.current.find(
					(f) =>
						f.path.startsWith("org:") && isFolderExpanded(f.path),
				);
				parentPath = expandedOrg?.path || filesRef.current[0].path;
			}

			await loadFiles(parentPath);

			setCreatingItem(null);
			setNewItemName("");
			setCreatingInFolder(null);
		} catch (err) {
			toast.error(`Failed to create ${creatingItem}`, {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [
		newItemName,
		creatingItem,
		creatingInFolder,
		loadFiles,
		operations,
		config,
		isFolderExpanded,
	]);

	// Focus input when creating new item
	useEffect(() => {
		if (creatingItem && inputRef.current) {
			inputRef.current.focus();
		}
	}, [creatingItem, creatingInFolder]);

	// Handle clicks outside the input to cancel if empty
	useEffect(() => {
		if (!creatingItem) return;

		const handleClickOutside = (event: MouseEvent) => {
			if (
				inputRef.current &&
				!inputRef.current.contains(event.target as Node)
			) {
				if (!newItemName.trim()) {
					handleCancelNewItem();
				}
			}
		};

		document.addEventListener("mousedown", handleClickOutside);
		return () => {
			document.removeEventListener("mousedown", handleClickOutside);
		};
	}, [creatingItem, newItemName, handleCancelNewItem]);

	const handleNewItemKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === "Enter") {
				e.preventDefault();
				if (newItemName.trim()) {
					handleSaveNewItem();
				} else {
					handleCancelNewItem();
				}
			} else if (e.key === "Escape") {
				e.preventDefault();
				handleCancelNewItem();
			}
		},
		[handleSaveNewItem, handleCancelNewItem, newItemName],
	);

	const handleDelete = useCallback((file: FileNode) => {
		setFileToDelete(file);
	}, []);

	const handleConfirmDelete = useCallback(async () => {
		if (!fileToDelete) return;

		const isFolder = fileToDelete.type === "folder";
		const deletePath = fileToDelete.path;
		const deleteName = fileToDelete.name;

		try {
			setIsProcessing(true);

			// Optimistically remove from tree
			removeFromTree(deletePath, isFolder);
			setFileToDelete(null);

			// Delete from server
			await operations.delete(deletePath);
			// Keep open tabs intact until the server confirms deletion.
			editor?.onFileDeleted?.(deletePath, isFolder);

			toast.success(`Deleted ${deleteName}`);
		} catch (err) {
			// On error, reload parent folder to restore correct state
			const parentFolder = deletePath.includes("/")
				? deletePath.substring(0, deletePath.lastIndexOf("/"))
				: "";
			await loadFiles(parentFolder);
			toast.error("Failed to delete", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [fileToDelete, loadFiles, editor, removeFromTree, operations]);

	const handleRename = useCallback((file: FileNode) => {
		setRenamingFile(file);
		setRenameValue(file.name);
	}, []);

	const handleCancelRename = useCallback(() => {
		setRenamingFile(null);
		setRenameValue("");
	}, []);

	const handleSaveRename = useCallback(async () => {
		if (
			!renamingFile ||
			!renameValue.trim() ||
			renameValue === renamingFile.name
		) {
			handleCancelRename();
			return;
		}

		const newPath = renamingFile.path.includes("/")
			? renamingFile.path.replace(/[^/]+$/, renameValue)
			: renameValue;

		// Validate path if validator is configured
		if (config.pathValidator) {
			const validation = config.pathValidator(newPath);
			if (!validation.valid) {
				toast.error("Invalid path", {
					description: validation.error,
				});
				return;
			}
		}

		try {
			setIsProcessing(true);

			const parentFolder = renamingFile.path.includes("/")
				? renamingFile.path.substring(
						0,
						renamingFile.path.lastIndexOf("/"),
					)
				: "";

			await operations.rename(renamingFile.path, newPath);
			editor?.onFileRenamed?.(renamingFile.path, newPath);
			await loadFiles(parentFolder);
			toast.success(`Renamed to ${renameValue}`);

			handleCancelRename();
		} catch (err) {
			toast.error("Failed to rename", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [
		renamingFile,
		renameValue,
		loadFiles,
		handleCancelRename,
		editor,
		operations,
		config,
	]);

	// Focus rename input when renaming starts
	useEffect(() => {
		if (renamingFile && renameInputRef.current) {
			renameInputRef.current.focus();
			const lastDotIndex = renameValue.lastIndexOf(".");
			if (lastDotIndex > 0) {
				renameInputRef.current.setSelectionRange(0, lastDotIndex);
			} else {
				renameInputRef.current.select();
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [renamingFile]);

	// Handle clicks outside the rename input to save
	useEffect(() => {
		if (!renamingFile) return;

		const handleClickOutside = (event: MouseEvent) => {
			if (
				renameInputRef.current &&
				!renameInputRef.current.contains(event.target as Node)
			) {
				if (renameValue.trim()) {
					handleSaveRename();
				} else {
					handleCancelRename();
				}
			}
		};

		document.addEventListener("mousedown", handleClickOutside);
		return () => {
			document.removeEventListener("mousedown", handleClickOutside);
		};
	}, [renamingFile, renameValue, handleSaveRename, handleCancelRename]);

	const handleRenameKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === "Enter") {
				e.preventDefault();
				if (renameValue.trim()) {
					handleSaveRename();
				} else {
					handleCancelRename();
				}
			} else if (e.key === "Escape") {
				e.preventDefault();
				handleCancelRename();
			}
		},
		[handleSaveRename, handleCancelRename, renameValue],
	);

	const handleRenameInputMouseDown = useCallback((e: React.MouseEvent) => {
		e.stopPropagation();
	}, []);

	const handleDragStart = useCallback(
		(e: React.DragEvent, file: FileNode) => {
			e.dataTransfer.effectAllowed = "move";
			e.dataTransfer.setData("text/plain", file.path);
			e.dataTransfer.setData("application/json", JSON.stringify(file));
		},
		[],
	);

	const handleDragOver = useCallback(
		(e: React.DragEvent, targetFolder?: string) => {
			e.preventDefault();
			e.dataTransfer.dropEffect = "move";
			setDragOverFolder(targetFolder || "");
		},
		[],
	);

	const handleDragLeave = useCallback(() => {
		setDragOverFolder(null);
	}, []);

	const handleDrop = useCallback(
		async (e: React.DragEvent, targetFolder?: string) => {
			e.preventDefault();
			setDragOverFolder(null);

			if (!config.enableDragMove) return;

			// Internal move operation
			try {
				const draggedPath = e.dataTransfer.getData("text/plain");
				if (!draggedPath) return;

				// Don't allow dropping on itself
				if (draggedPath === targetFolder) return;

				// Don't allow dropping a folder into its own child
				if (targetFolder && targetFolder.startsWith(draggedPath + "/"))
					return;

				// Calculate new path
				const fileName = draggedPath.split("/").pop()!;
				const newPath = targetFolder
					? `${targetFolder}/${fileName}`
					: fileName;

				// Don't do anything if the path hasn't changed
				if (draggedPath === newPath) return;

				// Validate path if validator is configured
				if (config.pathValidator) {
					const validation = config.pathValidator(newPath);
					if (!validation.valid) {
						toast.error("Cannot move here", {
							description: validation.error,
						});
						return;
					}
				}

				setIsProcessing(true);

				const sourceFolder = draggedPath.includes("/")
					? draggedPath.substring(0, draggedPath.lastIndexOf("/"))
					: "";
				const targetFolderPath = targetFolder || "";

				await operations.rename(draggedPath, newPath);
				editor?.onFileRenamed?.(draggedPath, newPath);

				// Reload affected folders
				await loadFiles(sourceFolder);
				if (sourceFolder !== targetFolderPath) {
					await loadFiles(targetFolderPath);
				}

				toast.success(`Moved ${fileName}`);
			} catch (err) {
				toast.error("Failed to move", {
					description:
						err instanceof Error ? err.message : String(err),
				});
			} finally {
				setIsProcessing(false);
			}
		},
		[loadFiles, editor, operations, config],
	);

	return (
		<div className={cn("flex h-full min-h-0 min-w-0 flex-col", className)}>
			{/* Toolbar */}
			<div className="flex items-center gap-1 border-b p-2">
				{config.enableCreate && (
					<>
						<Button
							variant="ghost"
							size="sm"
							onClick={() => handleCreateFile()}
							aria-label="New File"
							type="button"
							title="New File"
							className="h-11 min-w-11 px-2"
							disabled={isProcessing}
						>
							<FilePlus className="h-4 w-4" />
						</Button>
						<Button
							variant="ghost"
							size="sm"
							onClick={() => handleCreateFolder()}
							aria-label="New Folder"
							type="button"
							title="New Folder"
							className="h-11 min-w-11 px-2"
							disabled={isProcessing}
						>
							<FolderPlus className="h-4 w-4" />
						</Button>
					</>
				)}
				<Button
					variant="ghost"
					size="sm"
					onClick={handleRefresh}
					aria-label="Refresh"
					type="button"
					title="Refresh"
					className="h-11 min-w-11 px-2"
					disabled={isProcessing}
				>
					<RefreshCw
						className={cn(
							"h-4 w-4",
							isProcessing && "motion-safe:animate-spin",
						)}
					/>
				</Button>
			</div>

			{/* File list */}
			<div
				className={cn(
					"min-h-0 min-w-0 flex-1 overflow-auto",
					dragOverFolder === "" &&
						"bg-primary/10 outline outline-2 outline-primary outline-dashed",
				)}
				onDragOver={(e) => handleDragOver(e)}
				onDragLeave={handleDragLeave}
				onDrop={(e) => handleDrop(e)}
			>
				{failedPaths.length > 0 && (
					<div className="p-2">
						<FileTreeReadError
							paths={failedPaths}
							loading={isLoading}
							onRetry={() => {
								void Promise.all(failedPaths.map(loadFiles));
							}}
						/>
					</div>
				)}
				{isLoading && files.length === 0 && !creatingItem ? (
					<div className="flex h-full items-center justify-center">
						<div className="text-sm text-muted-foreground">
							{config.loadingMessage}
						</div>
					</div>
				) : files.length === 0 &&
				  !creatingItem &&
				  failedPaths.length === 0 ? (
					<div className="flex h-full items-center justify-center p-4">
						<div className="text-center text-sm text-muted-foreground">
							<p>{config.emptyMessage}</p>
							{config.enableCreate && (
								<p className="mt-2 text-xs">
									Use the toolbar to create files and folders
								</p>
							)}
						</div>
					</div>
				) : (
					<div className="space-y-1 p-2">
						{/* Inline new item editor at root */}
						{creatingItem && !creatingInFolder && (
							<div className="flex items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-1 bg-muted/50">
								<div className="w-4 shrink-0" />
								{isProcessing ? (
									<Loader2 className="h-4 w-4 flex-shrink-0 motion-safe:animate-spin text-primary" />
								) : creatingItem === "folder" ? (
									<Folder className="h-4 w-4 flex-shrink-0 text-primary" />
								) : (
									<File className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
								)}
								<Input
									aria-label={
										creatingItem === "folder"
											? "Folder name"
											: "File name"
									}
									ref={inputRef}
									type="text"
									value={newItemName}
									onChange={(e) =>
										setNewItemName(e.target.value)
									}
									onKeyDown={handleNewItemKeyDown}
									onMouseDown={handleInputMouseDown}
									placeholder={
										creatingItem === "folder"
											? "Folder name"
											: "File name"
									}
									disabled={isProcessing}
									className="min-w-0 min-h-11 flex-1 text-base sm:text-sm"
								/>
							</div>
						)}

						{/* Existing files */}
						{files.map((file) => (
							<FileTreeItem
								key={file.path}
								file={file}
								iconResolver={iconResolver}
								config={config}
								editor={editor}
								onFileClick={handleFileClick}
								onFolderToggle={handleFolderToggle}
								onDelete={handleDelete}
								onRename={handleRename}
								onCreateFile={handleCreateFile}
								onCreateFolder={handleCreateFolder}
								onChangeScope={onChangeScope}
								isExpanded={isFolderExpanded(file.path)}
								isLoadingContents={isFolderLoading(file.path)}
								onDragStart={handleDragStart}
								onDragOver={handleDragOver}
								onDragLeave={handleDragLeave}
								onDrop={handleDrop}
								isDragOver={dragOverFolder === file.path}
								creatingItem={creatingItem}
								creatingInFolder={creatingInFolder}
								newItemName={newItemName}
								setNewItemName={setNewItemName}
								inputRef={inputRef}
								handleNewItemKeyDown={handleNewItemKeyDown}
								handleInputMouseDown={handleInputMouseDown}
								renamingFile={renamingFile}
								renameValue={renameValue}
								setRenameValue={setRenameValue}
								renameInputRef={renameInputRef}
								handleRenameKeyDown={handleRenameKeyDown}
								handleRenameInputMouseDown={
									handleRenameInputMouseDown
								}
								isProcessing={isProcessing}
							/>
						))}
					</div>
				)}
			</div>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={!!fileToDelete}
				onOpenChange={(open) => !open && setFileToDelete(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete{" "}
							{fileToDelete?.type === "folder"
								? "Folder"
								: "File"}
						</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete{" "}
							<strong>{fileToDelete?.name}</strong>?
							{fileToDelete?.type === "folder" &&
								" This will delete all contents inside the folder."}{" "}
							This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

interface FileTreeItemProps {
	file: FileTreeNode;
	iconResolver: FileIconResolver;
	config: ResolvedConfig;
	editor?: EditorCallbacks;
	onFileClick: (file: FileNode) => void;
	onFolderToggle: (folder: FileNode) => void;
	onDelete: (file: FileNode) => void;
	onRename: (file: FileNode) => void;
	onCreateFile: (folderPath?: string) => void;
	onCreateFolder: (folderPath?: string) => void;
	onChangeScope?: (file: FileNode) => void;
	isExpanded: boolean;
	isLoadingContents: boolean;
	onDragStart: (e: React.DragEvent, file: FileNode) => void;
	onDragOver: (e: React.DragEvent, targetFolder?: string) => void;
	onDragLeave: () => void;
	onDrop: (e: React.DragEvent, targetFolder?: string) => void;
	isDragOver: boolean;
	creatingItem: CreatingItemType;
	creatingInFolder: string | null;
	newItemName: string;
	setNewItemName: (name: string) => void;
	inputRef: React.RefObject<HTMLInputElement | null>;
	handleNewItemKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
	handleInputMouseDown: (e: React.MouseEvent) => void;
	renamingFile: FileNode | null;
	renameValue: string;
	setRenameValue: (name: string) => void;
	renameInputRef: React.RefObject<HTMLInputElement | null>;
	handleRenameKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
	handleRenameInputMouseDown: (e: React.MouseEvent) => void;
	isProcessing: boolean;
}

const FileTreeItem = memo(function FileTreeItem({
	file,
	iconResolver,
	config,
	editor,
	onFileClick,
	onFolderToggle,
	onDelete,
	onRename,
	onCreateFile,
	onCreateFolder,
	onChangeScope,
	isExpanded,
	isLoadingContents,
	onDragStart,
	onDragOver,
	onDragLeave,
	onDrop,
	isDragOver,
	creatingItem,
	creatingInFolder,
	newItemName,
	setNewItemName,
	inputRef,
	handleNewItemKeyDown,
	handleInputMouseDown,
	renamingFile,
	renameValue,
	setRenameValue,
	renameInputRef,
	handleRenameKeyDown,
	handleRenameInputMouseDown,
	isProcessing,
}: FileTreeItemProps) {
	const isFolder = file.type === "folder";
	const level = file.level;
	const isRenaming = renamingFile?.path === file.path;
	const isSelected = editor?.isFileSelected?.(file.path) ?? false;
	const organizationName = file.metadata?.organizationName as
		string | undefined;

	// Can drag any file/folder
	const canDrag = config.enableDragMove;

	// Get icon from resolver
	const { icon: FileIcon, className: iconClassName } = iconResolver(file);

	return (
		<div>
			{isRenaming ? (
				// Inline rename editor
				<div
					className="flex items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-1 bg-muted/50"
					style={{ paddingLeft: `min(${level * 12 + 8}px, 25%)` }}
				>
					{isFolder ? (
						<>
							{isLoadingContents ? (
								<Loader2 className="h-4 w-4 flex-shrink-0 motion-safe:animate-spin text-muted-foreground" />
							) : isExpanded ? (
								<ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
							) : (
								<ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
							)}
							<FileIcon
								className={cn(
									"h-4 w-4 flex-shrink-0",
									iconClassName,
								)}
							/>
						</>
					) : (
						<>
							<div className="w-4 shrink-0" />
							<FileIcon
								className={cn(
									"h-4 w-4 flex-shrink-0",
									iconClassName,
								)}
							/>
						</>
					)}
					<Input
						aria-label={`Rename ${file.name}`}
						ref={renameInputRef}
						type="text"
						value={renameValue}
						onChange={(e) => setRenameValue(e.target.value)}
						onKeyDown={handleRenameKeyDown}
						onMouseDown={handleRenameInputMouseDown}
						disabled={isProcessing}
						className="min-w-0 min-h-11 flex-1 text-base sm:text-sm"
					/>
				</div>
			) : (
				<div className="flex min-w-0 items-start gap-1">
					<ContextMenu>
						<ContextMenuTrigger asChild>
							<button
								type="button"
								aria-expanded={
									isFolder ? isExpanded : undefined
								}
								aria-pressed={
									!isFolder ? isSelected : undefined
								}
								draggable={canDrag}
								onClick={() => {
									if (isFolder) {
										onFolderToggle(file);
									} else {
										onFileClick(file);
									}
								}}
								onDragStart={
									canDrag
										? (e) => onDragStart(e, file)
										: undefined
								}
								onDragOver={(e) => {
									if (isFolder) {
										e.stopPropagation();
										onDragOver(e, file.path);
									}
								}}
								onDragLeave={onDragLeave}
								onDrop={(e) => {
									if (isFolder) {
										e.stopPropagation();
										onDrop(e, file.path);
									}
								}}
								className={cn(
									"flex min-h-11 min-w-0 w-full items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-1 text-left text-sm transition-colors motion-reduce:transition-none outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
									isSelected && !isDragOver
										? "bg-accent text-accent-foreground"
										: "",
									!isDragOver && !isSelected
										? "hover:bg-muted"
										: "",
									isDragOver &&
										isFolder &&
										"bg-primary/10 ring-2 ring-inset ring-primary",
								)}
								style={{
									paddingLeft: `min(${level * 12 + 8}px, 25%)`,
								}}
							>
								{isFolder ? (
									<>
										{isLoadingContents ? (
											<Loader2 className="h-4 w-4 flex-shrink-0 motion-safe:animate-spin text-muted-foreground" />
										) : isExpanded ? (
											<ChevronDown className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
										) : (
											<ChevronRight className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
										)}
										<FileIcon
											className={cn(
												"h-4 w-4 flex-shrink-0",
												iconClassName,
											)}
										/>
									</>
								) : (
									<>
										<div className="w-4 shrink-0" />
										<FileIcon
											className={cn(
												"h-4 w-4 flex-shrink-0",
												iconClassName,
											)}
										/>
									</>
								)}
								<div className="flex-1 min-w-0">
									<span className="block [overflow-wrap:anywhere]">
										{file.name}
									</span>
									{/* Scope indicator for org-scoped files */}
									{organizationName && !isFolder && (
										<span className="flex items-center gap-1 text-xs text-muted-foreground [overflow-wrap:anywhere]">
											{organizationName}
											{!file.entityType && (
												<span title="Scope cannot be changed for this file">
													<Lock className="h-3 w-3 flex-shrink-0 opacity-50" />
												</span>
											)}
										</span>
									)}
								</div>
							</button>
						</ContextMenuTrigger>
						<ContextMenuContent className="z-[101]">
							<FileTreeActionItems
								mode="context"
								file={file}
								config={config}
								onCreateFile={onCreateFile}
								onCreateFolder={onCreateFolder}
								onRename={onRename}
								onDelete={onDelete}
								onChangeScope={onChangeScope}
							/>
						</ContextMenuContent>
					</ContextMenu>
					{(config.enableRename ||
						config.enableDelete ||
						(isFolder && config.enableCreate) ||
						onChangeScope) && (
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									type="button"
									variant="ghost"
									size="icon"
									className="size-11 shrink-0"
									aria-label={`Actions for ${file.name}`}
									disabled={isProcessing}
								>
									<MoreHorizontal className="size-4" />
								</Button>
							</DropdownMenuTrigger>
								<DropdownMenuContent
									align="end"
									className="z-[101] w-56 max-w-[calc(100vw-2rem)]"
								>
									<FileTreeActionItems
									mode="dropdown"
									file={file}
									config={config}
									onCreateFile={onCreateFile}
									onCreateFolder={onCreateFolder}
									onRename={onRename}
									onDelete={onDelete}
									onChangeScope={onChangeScope}
								/>
							</DropdownMenuContent>
						</DropdownMenu>
					)}
				</div>
			)}

			{/* Inline new item editor (shown when creating in this folder) */}
			{creatingItem && creatingInFolder === file.path && (
				<div
					className="flex items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-1 bg-muted/50 mt-1"
					style={{
						paddingLeft: `min(${(level + 1) * 12 + 8}px, 25%)`,
					}}
				>
					<div className="w-4 shrink-0" />
					{isProcessing ? (
						<Loader2 className="h-4 w-4 flex-shrink-0 motion-safe:animate-spin text-primary" />
					) : creatingItem === "folder" ? (
						<Folder className="h-4 w-4 flex-shrink-0 text-primary" />
					) : (
						<File className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
					)}
					<Input
						aria-label={
							creatingItem === "folder"
								? "Folder name"
								: "File name"
						}
						ref={inputRef}
						type="text"
						value={newItemName}
						onChange={(e) => setNewItemName(e.target.value)}
						onKeyDown={handleNewItemKeyDown}
						onMouseDown={handleInputMouseDown}
						placeholder={
							creatingItem === "folder"
								? "Folder name"
								: "File name"
						}
						disabled={isProcessing}
						className="min-w-0 min-h-11 flex-1 text-base sm:text-sm"
					/>
				</div>
			)}
		</div>
	);
});

// Re-export types for convenience
export type {
	FileNode,
	FileTreeNode,
	FileOperations,
	EditorCallbacks,
	FileTreeConfig,
};

/** Shared action definitions for the row button and desktop context menu. */
function FileTreeActionItems({
	mode,
	file,
	config,
	onCreateFile,
	onCreateFolder,
	onRename,
	onDelete,
	onChangeScope,
}: Pick<
	FileTreeItemProps,
	| "file"
	| "config"
	| "onCreateFile"
	| "onCreateFolder"
	| "onRename"
	| "onDelete"
	| "onChangeScope"
> & { mode: "context" | "dropdown" }) {
	const isFolder = file.type === "folder";
	const Item = mode === "context" ? ContextMenuItem : DropdownMenuItem;
	const Separator =
		mode === "context" ? ContextMenuSeparator : DropdownMenuSeparator;
	const itemClassName =
		mode === "context" ? undefined : "min-h-11 sm:min-h-7";

	return (
		<>
			{isFolder && config.enableCreate && (
				<>
					<Item onSelect={() => onCreateFile(file.path)}>
						<FilePlus className="h-4 w-4" />
						New File
					</Item>
					<Item onSelect={() => onCreateFolder(file.path)}>
						<FolderPlus className="h-4 w-4" />
						New Folder
					</Item>
					<Separator />
				</>
			)}
			{config.enableRename && (
				<Item
					onSelect={() => onRename(file)}
					className={cn(
						itemClassName,
						mode === "dropdown" && "text-foreground",
					)}
				>
					<Edit2 className="h-4 w-4" />
					Rename
				</Item>
			)}
			{config.enableDelete && (
				<>
					{config.enableRename && <Separator />}
					<Item
						onSelect={() => onDelete(file)}
						className={cn(
							itemClassName,
							"text-destructive focus:text-destructive",
						)}
					>
						<Trash2 className="h-4 w-4" />
						Delete
					</Item>
				</>
			)}
			{/* Change Scope option for entity files */}
			{onChangeScope && (
				<>
					<Separator />
					{file.entityType ? (
						<Item
							onSelect={() => onChangeScope(file)}
							className={cn(
								itemClassName,
								mode === "dropdown" && "text-foreground",
							)}
						>
							<Building2 className="h-4 w-4" />
							Change Scope...
						</Item>
					) : (
						<Item disabled className={itemClassName}>
							<Lock className="h-4 w-4" />
							Scope cannot be changed
						</Item>
					)}
				</>
			)}
		</>
	);
}
