import { useEffect, useRef, useState } from "react";
import { Download, Eye, FileText, Folder, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuSeparator,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { files } from "@/lib/app-sdk/files";
import { listStructure, type StructureEntry } from "@/services/fileStructure";
import { EntryMenuItem } from "./fileContextMenu";
import { InlineLoader } from "./InlineLoader";
import { useFileUpload } from "./useFileUpload";

export type ListingRowAction =
	| "preview"
	| "download"
	| "delete"
	| "policy"
	| "test";

export type ListingFolderAction = "effective" | "test" | "newPolicy" | "upload";

interface FolderListingProps {
	scope: string | null;
	location: string | null;
	prefix: string;
	readOnly: boolean;
	managedBySolution?: boolean;
	solutionId?: string | null;
	onOpenFolder: (prefix: string) => void;
	onSelectFile: (path: string) => void;
	onRowAction: (action: ListingRowAction, path: string) => void;
	onFolderAction: (action: ListingFolderAction, prefix: string) => void;
	onUploaded: () => void;
}

export function FolderListing({
	scope,
	location,
	prefix,
	readOnly,
	managedBySolution = false,
	solutionId = null,
	onOpenFolder,
	onSelectFile,
	onRowAction,
	onFolderAction,
	onUploaded,
}: FolderListingProps) {
	const [entries, setEntries] = useState<StructureEntry[]>([]);
	const [loading, setLoading] = useState(false);
	const [dragOver, setDragOver] = useState(false);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const { uploading, uploadFiles } = useFileUpload(
		readOnly ? null : location,
		scope,
		prefix,
		onUploaded,
	);

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			if (location === null) {
				setEntries([]);
				return;
			}
			setLoading(true);
			try {
				const result = await listStructure(location, prefix, scope);
				if (!cancelled) setEntries(result);
			} catch {
				if (!cancelled) setEntries([]);
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [location, prefix, scope]);

	async function handleDownload(path: string) {
		if (location === null) return;
		const blob = await files.download(path, { location, scope });
		if (typeof URL.createObjectURL !== "function") return;
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = path.split("/").at(-1) ?? "download";
		link.click();
		URL.revokeObjectURL(url);
	}

	const folders = entries.filter((e) => e.kind === "folder");
	const fileEntries = entries.filter((e) => e.kind === "file");
	const canUpload = !readOnly && location !== null;
	const managedBadge = managedBySolution ? (
		<SolutionManagedBadge solutionId={solutionId ?? undefined} />
	) : null;

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
				if (event.currentTarget.contains(event.relatedTarget as Node)) return;
				setDragOver(false);
			}}
			onDrop={(event) => {
				event.preventDefault();
				setDragOver(false);
				if (event.dataTransfer.files.length) void uploadFiles(event.dataTransfer.files);
			}}
		>
			{canUpload && (
				<input
					ref={fileInputRef}
					type="file"
					multiple
					className="hidden"
					onChange={(event) => {
						if (event.target.files?.length) void uploadFiles(event.target.files);
						event.target.value = "";
					}}
				/>
			)}
			{/* Full-pane drag overlay — visible while dragging files over the
			    listing, so the whole area reads as a dropzone. */}
			{dragOver && canUpload && (
				<div className="pointer-events-none absolute inset-0 z-10 m-1 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary/60 bg-primary/5 backdrop-blur-[1px]">
					<div className="flex flex-col items-center gap-2 text-sm font-medium text-primary">
						<Upload className="h-6 w-6" />
						Drop to upload to {prefix || "/"}
					</div>
				</div>
			)}
			<div className="min-h-0 flex-1 overflow-auto p-2">
				{location === null ? (
					<p className="p-4 text-sm text-muted-foreground">
						Choose a share from the tree to browse its files.
					</p>
				) : loading ? (
					<InlineLoader className="p-4" />
				) : entries.length === 0 ? (
					canUpload ? (
						<button
							type="button"
							onClick={() => fileInputRef.current?.click()}
							disabled={uploading}
							className="flex h-full min-h-[12rem] w-full flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border p-6 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
						>
							<Upload className="h-7 w-7" />
							<span className="font-medium">
								{uploading ? "Uploading…" : "Drag files here or click to upload"}
							</span>
							<span className="text-xs">Uploads to {prefix || "/"}</span>
						</button>
					) : (
						<p className="p-4 text-sm text-muted-foreground">No files here.</p>
					)
				) : (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Name</DataTableHead>
								<DataTableHead className="w-32 text-right">Actions</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{folders.map((folder) => (
								<ContextMenu key={folder.path}>
									<ContextMenuTrigger asChild>
										<DataTableRow
											clickable
											onClick={() => onOpenFolder(folder.path)}
										>
											<DataTableCell>
												<div className="flex min-w-0 items-center gap-2">
													<Folder className="h-4 w-4 text-muted-foreground" />
													<span className="truncate">{folder.name}</span>
													{managedBadge}
												</div>
											</DataTableCell>
											<DataTableCell />
										</DataTableRow>
									</ContextMenuTrigger>
									<ContextMenuContent>
										<EntryMenuItem
											action="effective"
											onSelect={() => onFolderAction("effective", folder.path)}
										/>
										<EntryMenuItem
											action="test"
											onSelect={() => onFolderAction("test", folder.path)}
										/>
										{!readOnly && (
											<>
												<EntryMenuItem
													action="upload"
													onSelect={() => onFolderAction("upload", folder.path)}
												/>
												<EntryMenuItem
													action="newPolicy"
													onSelect={() => onFolderAction("newPolicy", folder.path)}
												/>
											</>
										)}
									</ContextMenuContent>
								</ContextMenu>
							))}
							{fileEntries.map((file) => (
								<ContextMenu key={file.path}>
									<ContextMenuTrigger asChild>
										<DataTableRow clickable onClick={() => onSelectFile(file.path)}>
											<DataTableCell>
												<div className="flex min-w-0 items-center gap-2">
													<FileText className="h-4 w-4 text-muted-foreground" />
													<span className="truncate">{file.name}</span>
													{managedBadge}
												</div>
											</DataTableCell>
											<DataTableCell>
												<div className="flex justify-end gap-1">
													<Button
														type="button"
														variant="ghost"
														size="icon-xs"
														aria-label={`Preview ${file.name}`}
														title="Preview"
														onClick={(event) => {
															event.stopPropagation();
															onRowAction("preview", file.path);
														}}
													>
														<Eye className="h-3 w-3" />
													</Button>
													<Button
														type="button"
														variant="ghost"
														size="icon-xs"
														aria-label={`Download ${file.name}`}
														title="Download"
														onClick={(event) => {
															event.stopPropagation();
															void handleDownload(file.path);
														}}
													>
														<Download className="h-3 w-3" />
													</Button>
													{!readOnly && (
														<Button
															type="button"
															variant="ghost"
															size="icon-xs"
															aria-label={`Delete ${file.name}`}
															title="Delete"
															onClick={(event) => {
																event.stopPropagation();
																onRowAction("delete", file.path);
															}}
														>
															<Trash2 className="h-3 w-3" />
														</Button>
													)}
												</div>
											</DataTableCell>
										</DataTableRow>
									</ContextMenuTrigger>
									<ContextMenuContent>
										<EntryMenuItem action="preview" onSelect={() => onRowAction("preview", file.path)} />
										<EntryMenuItem action="test" onSelect={() => onRowAction("test", file.path)} />
										{!readOnly && (
											<EntryMenuItem action="policy" onSelect={() => onRowAction("policy", file.path)} />
										)}
										<EntryMenuItem action="download" onSelect={() => void handleDownload(file.path)} />
										{!readOnly && (
											<>
												<ContextMenuSeparator />
												<EntryMenuItem action="delete" destructive onSelect={() => onRowAction("delete", file.path)} />
											</>
										)}
									</ContextMenuContent>
								</ContextMenu>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</div>
		</section>
	);
}
