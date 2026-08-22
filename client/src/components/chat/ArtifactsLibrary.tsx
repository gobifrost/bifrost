import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	FileSpreadsheet,
	FileText,
	Image,
	MoreHorizontal,
	Pencil,
	Presentation,
	Search,
	Trash2,
	Video,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
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
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
	deleteChatArtifact,
	formatBytes,
	isImageAttachment,
	isVideoAttachment,
	listChatArtifacts,
	renameChatArtifact,
	type ChatArtifactPublic,
} from "@/services/chatAttachments";
import { FilePreviewSheet } from "./FilePreviewSheet";

type LibraryFilter = "all" | "artifact" | "attachment";

function ArtifactIcon({ artifact }: { artifact: ChatArtifactPublic }) {
	if (isImageAttachment(artifact.content_type)) return <Image className="h-5 w-5" />;
	if (isVideoAttachment(artifact.content_type)) return <Video className="h-5 w-5" />;
	if (artifact.content_type.includes("spreadsheet")) {
		return <FileSpreadsheet className="h-5 w-5" />;
	}
	if (artifact.content_type.includes("presentation")) {
		return <Presentation className="h-5 w-5" />;
	}
	return <FileText className="h-5 w-5" />;
}

function formatArtifactDate(value: string): string {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		year: "numeric",
	}).format(new Date(value));
}

export function ArtifactsLibrary() {
	const queryClient = useQueryClient();
	const [search, setSearch] = useState("");
	const [filter, setFilter] = useState<LibraryFilter>("all");
	const [preview, setPreview] = useState<ChatArtifactPublic | null>(null);
	const [renameTarget, setRenameTarget] = useState<ChatArtifactPublic | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<ChatArtifactPublic | null>(null);
	const [filename, setFilename] = useState("");
	const artifactsQuery = useQuery({
		queryKey: ["chat-artifacts"],
		queryFn: listChatArtifacts,
	});
	const renameMutation = useMutation({
		mutationFn: ({ id, name }: { id: string; name: string }) =>
			renameChatArtifact(id, name),
		onSuccess: (renamed) => {
			queryClient.setQueryData<ChatArtifactPublic[]>(
				["chat-artifacts"],
				(current = []) =>
					current.map((item) => (item.id === renamed.id ? renamed : item)),
			);
			setRenameTarget(null);
			toast.success("Artifact renamed");
		},
		onError: (error: Error) => toast.error(error.message),
	});
	const deleteMutation = useMutation({
		mutationFn: deleteChatArtifact,
		onSuccess: (_, id) => {
			queryClient.setQueryData<ChatArtifactPublic[]>(
				["chat-artifacts"],
				(current = []) => current.filter((item) => item.id !== id),
			);
			setDeleteTarget(null);
			toast.success("Artifact deleted");
		},
		onError: (error: Error) => toast.error(error.message),
	});

	const filtered = useMemo(() => {
		const term = search.trim().toLocaleLowerCase();
		return (artifactsQuery.data ?? []).filter((artifact) => {
			if (filter !== "all" && artifact.kind !== filter) return false;
			if (!term) return true;
			return [artifact.filename, artifact.conversation_title]
				.filter(Boolean)
				.some((value) => value!.toLocaleLowerCase().includes(term));
		});
	}, [artifactsQuery.data, filter, search]);

	const startRename = (artifact: ChatArtifactPublic) => {
		setRenameTarget(artifact);
		setFilename(artifact.filename);
	};

	return (
		<div className="min-h-0 flex-1 overflow-y-auto">
			<div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-8 sm:py-8">
				<div className="mb-7">
					<h1 className="text-2xl font-semibold tracking-tight">Artifacts</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						Files created or used in your conversations.
					</p>
				</div>

				<div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<div className="flex gap-1" aria-label="Artifact type">
						{(["all", "artifact", "attachment"] as const).map((value) => (
							<Button
								key={value}
								type="button"
								variant={filter === value ? "secondary" : "ghost"}
								size="sm"
								onClick={() => setFilter(value)}
								className="min-h-11 capitalize sm:min-h-7"
							>
								{value === "artifact" ? "Generated" : value === "attachment" ? "Uploaded" : value}
							</Button>
						))}
					</div>
					<label className="relative block w-full sm:w-72">
						<Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
						<span className="sr-only">Search artifacts</span>
						<Input
							value={search}
							onChange={(event) => setSearch(event.target.value)}
							placeholder="Search files"
							className="h-11 pl-9 sm:h-8"
						/>
					</label>
				</div>

				<div className="overflow-hidden rounded-2xl border bg-background">
					{artifactsQuery.isLoading ? (
						<div className="space-y-1 p-2">
							{[1, 2, 3, 4].map((item) => (
								<Skeleton key={item} className="h-16 w-full" />
							))}
						</div>
					) : artifactsQuery.isError ? (
						<div className="p-8 text-center">
							<p className="text-sm font-medium">Artifacts could not be loaded</p>
							<Button variant="outline" size="sm" className="mt-3" onClick={() => artifactsQuery.refetch()}>
								Try again
							</Button>
						</div>
					) : filtered.length === 0 ? (
						<div className="p-10 text-center">
							<FileText className="mx-auto h-7 w-7 text-muted-foreground" />
							<p className="mt-3 text-sm font-medium">
								{search ? "No matching files" : "No artifacts yet"}
							</p>
							<p className="mt-1 text-sm text-muted-foreground">
								Generated files and chat attachments will appear here.
							</p>
						</div>
					) : (
						<ul className="divide-y">
							{filtered.map((artifact) => (
								<li key={artifact.id} className="group flex min-h-16 items-center gap-3 px-3 py-2 hover:bg-muted/40">
									<button
										type="button"
										aria-label={`Preview ${artifact.filename}`}
										onClick={() => setPreview(artifact)}
										className="flex min-w-0 flex-1 items-center gap-3 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
									>
										<span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", artifact.kind === "artifact" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
											<ArtifactIcon artifact={artifact} />
										</span>
										<span className="min-w-0 flex-1">
											<span className="block truncate text-sm font-medium">{artifact.filename}</span>
											<span className="block truncate text-xs text-muted-foreground">
												{artifact.kind === "artifact" ? "Generated" : "Uploaded"} · {formatBytes(artifact.size_bytes)}
												{artifact.conversation_title ? ` · ${artifact.conversation_title}` : ""}
											</span>
										</span>
										<span className="hidden text-xs text-muted-foreground sm:block">{formatArtifactDate(artifact.created_at)}</span>
									</button>
									<DropdownMenu>
										<DropdownMenuTrigger asChild>
										<Button variant="ghost" size="icon-sm" className="size-11 sm:size-7" aria-label={`Manage ${artifact.filename}`}>
												<MoreHorizontal className="h-4 w-4" />
											</Button>
										</DropdownMenuTrigger>
										<DropdownMenuContent align="end">
											<DropdownMenuItem onSelect={() => startRename(artifact)}>
												<Pencil /> Rename
											</DropdownMenuItem>
											<DropdownMenuItem variant="destructive" onSelect={() => setDeleteTarget(artifact)}>
												<Trash2 /> Delete
											</DropdownMenuItem>
										</DropdownMenuContent>
									</DropdownMenu>
								</li>
							))}
						</ul>
					)}
				</div>
			</div>

			<FilePreviewSheet
				conversationId={preview?.conversation_id ?? ""}
				attachment={preview}
				attachments={filtered}
				onAttachmentChange={(attachment) =>
					setPreview(attachment as ChatArtifactPublic)
				}
				onOpenChange={(open) => !open && setPreview(null)}
			/>

			<Dialog open={renameTarget !== null} onOpenChange={(open) => !open && setRenameTarget(null)}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Rename artifact</DialogTitle>
						<DialogDescription>Choose the filename shown in Chat and your artifact library.</DialogDescription>
					</DialogHeader>
					<Input value={filename} onChange={(event) => setFilename(event.target.value)} autoFocus />
					<DialogFooter>
						<Button variant="ghost" onClick={() => setRenameTarget(null)}>Cancel</Button>
						<Button
							disabled={!filename.trim() || renameMutation.isPending}
							onClick={() => renameTarget && renameMutation.mutate({ id: renameTarget.id, name: filename.trim() })}
						>
							Rename
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<AlertDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete artifact?</AlertDialogTitle>
						<AlertDialogDescription>
							{deleteTarget?.filename} will be removed from its conversation and cannot be recovered.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
							onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
