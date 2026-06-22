import { useEffect, useRef, useState } from "react";
import { Download, Eye, FileText, Folder, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
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
import { files } from "@/lib/app-sdk/files";
import { listStructure, type StructureEntry } from "@/services/fileStructure";
import { EntryMenuItem } from "./fileContextMenu";

export type ListingRowAction =
	| "preview"
	| "download"
	| "delete"
	| "policy"
	| "test";

interface FolderListingProps {
	scope: string | null;
	location: string | null;
	prefix: string;
	readOnly: boolean;
	onOpenFolder: (prefix: string) => void;
	onSelectFile: (path: string) => void;
	onRowAction: (action: ListingRowAction, path: string) => void;
	onUploaded: () => void;
}

export function FolderListing({
	scope,
	location,
	prefix,
	readOnly,
	onOpenFolder,
	onSelectFile,
	onRowAction,
	onUploaded,
}: FolderListingProps) {
	const [entries, setEntries] = useState<StructureEntry[]>([]);
	const [loading, setLoading] = useState(false);
	const [dragOver, setDragOver] = useState(false);
	const [uploading, setUploading] = useState(false);
	const fileInputRef = useRef<HTMLInputElement>(null);

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

	async function uploadFiles(fileList: FileList | File[]) {
		if (location === null || readOnly) return;
		setUploading(true);
		try {
			for (const file of Array.from(fileList)) {
				const targetPath = prefix ? `${prefix.replace(/\/$/, "")}/${file.name}` : file.name;
				await files.upload(targetPath, file, { location, scope });
			}
			toast.success("Upload complete");
			onUploaded();
		} catch (err) {
			toast.error("Upload failed", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setUploading(false);
		}
	}

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

	return (
		<section
			className="flex min-h-0 flex-1 flex-col"
			onDragOver={(event) => {
				if (readOnly || location === null) return;
				event.preventDefault();
				setDragOver(true);
			}}
			onDragLeave={() => setDragOver(false)}
			onDrop={(event) => {
				event.preventDefault();
				setDragOver(false);
				if (event.dataTransfer.files.length) void uploadFiles(event.dataTransfer.files);
			}}
		>
			<div className="flex shrink-0 items-center justify-between border-b px-3 py-2">
				<p className="truncate text-sm text-muted-foreground" title={prefix}>
					{location === null ? "Select a share" : prefix || "/"}
				</p>
				{!readOnly && location !== null && (
					<>
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
						<Button
							type="button"
							size="xs"
							onClick={() => fileInputRef.current?.click()}
							disabled={uploading}
						>
							<Upload className="h-3 w-3" /> Upload
						</Button>
					</>
				)}
			</div>
			<div className="min-h-0 flex-1 overflow-auto">
				{dragOver && (
					<div className="m-2 rounded-md border-2 border-dashed border-primary/50 p-4 text-center text-xs text-muted-foreground">
						Drop files to upload to {prefix || "/"}
					</div>
				)}
				{location === null ? (
					<p className="p-4 text-sm text-muted-foreground">
						Choose a share from the tree to browse its files.
					</p>
				) : loading ? (
					<p className="p-4 text-sm text-muted-foreground">Loading…</p>
				) : entries.length === 0 ? (
					<p className="p-4 text-sm text-muted-foreground">No files here.</p>
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
								<DataTableRow
									key={folder.path}
									clickable
									onClick={() => onOpenFolder(folder.path)}
								>
									<DataTableCell>
										<div className="flex min-w-0 items-center gap-2">
											<Folder className="h-4 w-4 text-muted-foreground" />
											<span className="truncate">{folder.name}</span>
										</div>
									</DataTableCell>
									<DataTableCell />
								</DataTableRow>
							))}
							{fileEntries.map((file) => (
								<ContextMenu key={file.path}>
									<ContextMenuTrigger asChild>
										<DataTableRow clickable onClick={() => onSelectFile(file.path)}>
											<DataTableCell>
												<div className="flex min-w-0 items-center gap-2">
													<FileText className="h-4 w-4 text-muted-foreground" />
													<span className="truncate">{file.name}</span>
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
										<EntryMenuItem action="policy" onSelect={() => onRowAction("policy", file.path)} />
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
