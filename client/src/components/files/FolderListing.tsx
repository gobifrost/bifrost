import { useMemo, useRef, useState } from "react";
import {
	Code2,
	File,
	FileImage,
	FileJson,
	FileText,
	Folder,
	RotateCcw,
	Search,
	Upload,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { FileEntryActions } from "./FileEntryActions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	ContextMenu,
	ContextMenuSeparator,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { files } from "@/lib/app-sdk/files";
import { cn } from "@/lib/utils";
import { listStructure, type StructureEntry } from "@/services/fileStructure";
import { EntryMenuItem, FileContextMenuContent } from "./fileContextMenu";
import { InlineLoader } from "./InlineLoader";
import { useFileUpload } from "./useFileUpload";

export type ListingRowAction =
	"preview" | "download" | "delete" | "policy" | "test";

export type ListingFolderAction = "effective" | "test" | "newPolicy" | "upload";

interface FolderListingProps {
	scope: string | null;
	solution?: string;
	location: string | null;
	prefix: string;
	readOnly: boolean;
	policyReadOnly?: boolean;
	managedBySolution?: boolean;
	solutionId?: string | null;
	selectedPath?: string | null;
	onOpenFolder: (prefix: string) => void;
	onSelectFile: (path: string) => void;
	onRowAction: (action: ListingRowAction, path: string) => void;
	onFolderAction: (action: ListingFolderAction, prefix: string) => void;
	onUploaded: () => void;
}

export function FolderListing({
	scope,
	solution,
	location,
	prefix,
	readOnly,
	policyReadOnly = false,
	managedBySolution = false,
	solutionId = null,
	selectedPath = null,
	onOpenFolder,
	onSelectFile,
	onRowAction,
	onFolderAction,
	onUploaded,
}: FolderListingProps) {
	const listing = useQuery({
		queryKey: ["file-structure", scope, solution, location, prefix],
		queryFn: () =>
			solution
				? listStructure(location!, prefix, scope, solution)
				: listStructure(location!, prefix, scope),
		enabled: location !== null,
		retry: false,
	});
	const entries = useMemo(() => listing.data ?? [], [listing.data]);
	const loading = listing.isPending;
	const [downloadError, setDownloadError] = useState<{
		path: string;
		message: string;
	} | null>(null);
	const [downloading, setDownloading] = useState<string | null>(null);
	const [dragOver, setDragOver] = useState(false);
	const [search, setSearch] = useState("");
	const fileInputRef = useRef<HTMLInputElement>(null);
	const { uploading, uploadFiles, progress, error, retryUpload } =
		useFileUpload(readOnly ? null : location, scope, prefix, onUploaded);

	async function handleDownload(path: string) {
		if (location === null || downloading) return;
		setDownloading(path);
		setDownloadError(null);
		let url: string | undefined;
		try {
			const blob = await files.download(
				path,
				solution ? { location, scope, solution } : { location, scope },
			);
			if (typeof URL.createObjectURL !== "function")
				throw new Error("Downloads are unavailable in this browser.");
			url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = path.split("/").at(-1) ?? "download";
			link.click();
		} catch (error) {
			setDownloadError({
				path,
				message:
					error instanceof Error
						? error.message.replace(
								/^files\.download:\s*\d+\s*/,
								"",
							)
						: "Try downloading this file again.",
			});
		} finally {
			if (url) URL.revokeObjectURL(url);
			setDownloading(null);
		}
	}

	const sortedEntries = useMemo(
		() =>
			[...entries].sort((a, b) => {
				if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
				return a.name.localeCompare(b.name, undefined, {
					sensitivity: "base",
					numeric: true,
				});
			}),
		[entries],
	);
	const visibleEntries = useMemo(() => {
		const normalizedSearch = search.trim().toLocaleLowerCase();
		if (!normalizedSearch) return sortedEntries;
		return sortedEntries.filter((entry) =>
			`${entry.name} ${entry.path}`
				.toLocaleLowerCase()
				.includes(normalizedSearch),
		);
	}, [search, sortedEntries]);
	const canUpload = !readOnly && location !== null;
	const managedBadge = managedBySolution ? (
		<SolutionManagedBadge solutionId={solutionId ?? undefined} />
	) : null;

	const renderActions = (entry: StructureEntry) => (
		<FileEntryActions
			entry={entry}
			readOnly={readOnly}
			policyReadOnly={policyReadOnly}
			downloading={downloading !== null}
			onAction={(action, path) => {
				if (action === "download") void handleDownload(path);
				else if (entry.kind === "folder")
					onFolderAction(action as ListingFolderAction, path);
				else onRowAction(action as ListingRowAction, path);
			}}
		/>
	);

	const activateEntry = (entry: StructureEntry) => {
		if (entry.kind === "folder") onOpenFolder(entry.path);
		else onSelectFile(entry.path);
	};

	const renderContextMenu = (entry: StructureEntry) => {
		const isFolder = entry.kind === "folder";
		if (isFolder)
			return (
				<FileContextMenuContent>
					<EntryMenuItem
						action="effective"
						onSelect={() => onFolderAction("effective", entry.path)}
					/>
					<EntryMenuItem
						action="test"
						onSelect={() => onFolderAction("test", entry.path)}
					/>
					{!readOnly && (
						<EntryMenuItem
							action="upload"
							onSelect={() =>
								onFolderAction("upload", entry.path)
							}
						/>
					)}
					{!policyReadOnly && (
						<>
							<EntryMenuItem
								action="newPolicy"
								onSelect={() =>
									onFolderAction("newPolicy", entry.path)
								}
							/>
						</>
					)}
				</FileContextMenuContent>
			);

		return (
			<FileContextMenuContent>
				<EntryMenuItem
					action="preview"
					onSelect={() => onRowAction("preview", entry.path)}
				/>
				<EntryMenuItem
					action="test"
					onSelect={() => onRowAction("test", entry.path)}
				/>
				{!policyReadOnly && (
					<EntryMenuItem
						action="policy"
						onSelect={() => onRowAction("policy", entry.path)}
					/>
				)}
				<EntryMenuItem
					action="download"
					onSelect={() => void handleDownload(entry.path)}
				/>
				{!readOnly && (
					<>
						<ContextMenuSeparator />
						<EntryMenuItem
							action="delete"
							destructive
							onSelect={() => onRowAction("delete", entry.path)}
						/>
					</>
				)}
			</FileContextMenuContent>
		);
	};

	const renderEntry = (entry: StructureEntry) => {
		const isFolder = entry.kind === "folder";
		const Icon = entryIcon(entry);
		const selected = selectedPath === entry.path;

		return (
			<ContextMenu key={entry.path}>
				<ContextMenuTrigger asChild>
					<li
						aria-label={entry.name}
						aria-current={selected ? "true" : undefined}
						className={cn(
							"group flex min-h-12 items-center border-b border-border/70 transition-colors last:border-b-0 [overflow-wrap:anywhere] motion-reduce:transition-none",
							selected
								? "tree-row-selected"
								: "hover:bg-muted/20",
						)}
					>
						<button
							type="button"
							aria-label={entry.name}
							className="flex min-h-12 min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
							onClick={() => activateEntry(entry)}
						>
							<span
								className={cn(
									"flex size-8 shrink-0 items-center justify-center rounded-[var(--bf-radius-surface)] border",
									isFolder
										? "border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-400"
										: "border-border bg-muted/40 text-muted-foreground",
								)}
							>
								<Icon aria-hidden="true" className="size-4" />
							</span>
							<span className="min-w-0 flex-1">
								<span className="block truncate text-sm font-medium text-foreground">
									{entry.name}
								</span>
								<span className="block truncate text-xs text-muted-foreground">
									{isFolder ? "Folder" : "File"}
								</span>
							</span>
							{managedBadge && (
								<span className="hidden shrink-0 sm:inline-flex">
									{managedBadge}
								</span>
							)}
						</button>
						<span className="shrink-0 pr-1">
							{renderActions(entry)}
						</span>
					</li>
				</ContextMenuTrigger>
				{renderContextMenu(entry)}
			</ContextMenu>
		);
	};

	return (
		<section
			className="relative flex min-h-0 flex-1 flex-col"
			onDragOver={(event) => {
				if (readOnly || location === null) return;
				event.preventDefault();
				setDragOver(true);
			}}
			onDragLeave={(event) => {
				// Only clear when the cursor actually leaves the section, not
				// when it crosses a child element boundary.
				if (event.currentTarget.contains(event.relatedTarget as Node))
					return;
				setDragOver(false);
			}}
			onDrop={(event) => {
				event.preventDefault();
				setDragOver(false);
				if (event.dataTransfer.files.length)
					void uploadFiles(event.dataTransfer.files);
			}}
		>
			{canUpload && (
				<input
					ref={fileInputRef}
					type="file"
					multiple
					className="hidden"
					onChange={(event) => {
						if (event.target.files?.length)
							void uploadFiles(event.target.files);
						event.target.value = "";
					}}
				/>
			)}
			{/* Full-pane drag overlay — visible while dragging files over the
			    listing, so the whole area reads as a dropzone. */}
			{dragOver && canUpload && (
				<div className="pointer-events-none absolute inset-0 z-10 m-1 flex items-center justify-center rounded-[var(--bf-radius-feature)] border-2 border-dashed border-primary/60 bg-primary/5 backdrop-blur-[1px]">
					<div className="flex flex-col items-center gap-2 text-sm font-medium text-primary">
						<Upload className="h-6 w-6" />
						Drop to upload to {prefix || "/"}
					</div>
				</div>
			)}
			<div className="flex min-h-0 flex-1 flex-col">
				{downloadError && (
					<Alert variant="destructive" className="mb-3">
						<AlertTitle>Download failed</AlertTitle>
						<AlertDescription>
							<p className="[overflow-wrap:anywhere]">
								{downloadError.path}: {downloadError.message}
							</p>
							<Button
								variant="outline"
								className="mt-3 min-h-11"
								onClick={() =>
									void handleDownload(downloadError.path)
								}
							>
								Retry download
							</Button>
						</AlertDescription>
					</Alert>
				)}
				{downloading && (
					<p
						role="status"
						className="mb-3 text-sm text-muted-foreground [overflow-wrap:anywhere]"
					>
						Downloading {downloading}…
					</p>
				)}
				{progress && (
					<p
						role="status"
						aria-live="polite"
						className="mb-3 text-sm text-muted-foreground [overflow-wrap:anywhere]"
					>
						{progress}
					</p>
				)}
				{error && (
					<Alert variant="destructive" className="mb-3">
						<AlertTitle>Upload failed</AlertTitle>
						<AlertDescription>
							<p className="[overflow-wrap:anywhere]">{error}</p>
							<Button
								variant="outline"
								className="mt-3 min-h-11"
								onClick={() => retryUpload()}
							>
								<RotateCcw className="h-4 w-4" />
								Retry upload
							</Button>
						</AlertDescription>
					</Alert>
				)}
				{location !== null && listing.isError && (
					<Alert variant="destructive" className="mb-3">
						<AlertTitle>Folder could not be loaded</AlertTitle>
						<AlertDescription>
							<p>
								{listing.data
									? "Showing the last loaded items."
									: "Try loading this folder again."}
							</p>
							<Button
								variant="outline"
								className="mt-3 min-h-11"
								onClick={() => void listing.refetch()}
							>
								Retry folder
							</Button>
						</AlertDescription>
					</Alert>
				)}
				{location === null ? (
					<p className="p-4 text-sm text-muted-foreground">
						Choose a share to browse its folders and files.
					</p>
				) : loading ? (
					<InlineLoader label="Loading folder…" className="p-4" />
				) : listing.isError && !listing.data ? null : entries.length ===
				  0 ? (
					canUpload ? (
						<button
							type="button"
							onClick={() => fileInputRef.current?.click()}
							disabled={uploading}
							className="flex h-full min-h-[12rem] w-full flex-col items-center justify-center gap-2 rounded-[var(--bf-radius-feature)] border-2 border-dashed border-border p-6 text-sm text-muted-foreground transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:border-primary/50 hover:text-foreground"
						>
							<Upload className="h-7 w-7" />
							<span className="font-medium">
								{uploading
									? "Uploading…"
									: "Drag files here or click to upload"}
							</span>
							<span className="text-xs">
								Uploads to {prefix || "/"}
							</span>
						</button>
					) : (
						<p className="p-4 text-sm text-muted-foreground">
							No files here.
						</p>
					)
				) : (
					<div className="flex min-h-0 flex-1 flex-col">
						<div className="shrink-0 border-b border-border/70 p-2">
							<div className="relative">
								<Search
									aria-hidden="true"
									className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
								/>
								<Input
									value={search}
									onChange={(event) =>
										setSearch(event.target.value)
									}
									placeholder="Search this folder"
									aria-label="Search this folder"
									className="h-9 pl-9"
								/>
							</div>
						</div>
						{visibleEntries.length > 0 ? (
							<ul
								aria-label="Folders and files"
								className="min-w-0 flex-1 overflow-auto"
							>
								{visibleEntries.map(renderEntry)}
							</ul>
						) : (
							<div className="flex min-h-0 flex-1 flex-col items-start justify-center gap-3 overflow-auto px-4 py-8 text-sm text-muted-foreground">
								<p>
									No matches for "{search.trim()}" in this
									folder.
								</p>
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									onClick={() => setSearch("")}
								>
									Clear search
								</Button>
							</div>
						)}
						<p className="shrink-0 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
							{visibleEntries.length === entries.length
								? itemCountLabel(entries.length)
								: `${itemCountLabel(visibleEntries.length)} matching ${itemCountLabel(entries.length).toLocaleLowerCase()}`}
						</p>
					</div>
				)}
			</div>
		</section>
	);
}

function entryIcon(entry: StructureEntry) {
	if (entry.kind === "folder") return Folder;
	const extension = entry.name.split(".").pop()?.toLocaleLowerCase();
	if (!extension || extension === entry.name) return File;
	if (["gif", "jpeg", "jpg", "png", "svg", "webp"].includes(extension))
		return FileImage;
	if (["json", "jsonl"].includes(extension)) return FileJson;
	if (
		[
			"css",
			"html",
			"js",
			"jsx",
			"mdx",
			"py",
			"sh",
			"ts",
			"tsx",
			"yaml",
			"yml",
		].includes(extension)
	)
		return Code2;
	if (["csv", "log", "md", "txt"].includes(extension)) return FileText;
	return File;
}

function itemCountLabel(count: number) {
	if (count === 1) return "1 item";
	return `${count} items`;
}
