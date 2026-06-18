/**
 * Workspaces directory page.
 *
 * Lists workspaces visible to the current user as cards. Clicking a card
 * enters the workspace (`/chat?workspace=<id>`). The pencil opens settings
 * inline; the trash deletes (soft).
 *
 * Conversations not in any workspace live in the general pool and aren't
 * shown here — they're the unscoped chat list reachable from `/chat`.
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
	ArrowLeft,
	Edit3,
	FolderKanban,
	Lock,
	MessageSquare,
	Plus,
	Search,
	Trash2,
	Users,
} from "lucide-react";
import { toast } from "sonner";

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
import { CreateWorkspaceDialog } from "@/components/workspaces/CreateWorkspaceDialog";
import { WorkspaceSettingsSheet } from "@/components/workspaces/WorkspaceSettingsSheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/contexts/AuthContext";
import {
	useDeleteWorkspace,
	useWorkspace,
	useWorkspaces,
	type WorkspaceScope,
	type WorkspaceSummary,
} from "@/services/workspaceService";

const SCOPE_LABEL: Record<WorkspaceScope, string> = {
	personal: "Private",
	org: "Org",
	role: "Role",
};

export default function Workspaces() {
	const navigate = useNavigate();
	const { isPlatformAdmin, user } = useAuth();
	const { data: workspaces, isLoading } = useWorkspaces();
	const remove = useDeleteWorkspace();
	const [createOpen, setCreateOpen] = useState(false);
	const [editId, setEditId] = useState<string | null>(null);
	const [deleteTarget, setDeleteTarget] =
		useState<WorkspaceSummary | null>(null);
	const [search, setSearch] = useState("");

	const filtered = useMemo(() => {
		if (!workspaces) return undefined;
		const q = search.trim().toLowerCase();
		if (!q) return workspaces;
		return workspaces.filter((w) => {
			return (
				w.name.toLowerCase().includes(q) ||
				w.description?.toLowerCase().includes(q)
			);
		});
	}, [workspaces, search]);

	// Sort: alphabetical by name (no special-casing).
	const sorted = useMemo(() => {
		if (!filtered) return undefined;
		return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
	}, [filtered]);

	const handleDeleteConfirm = () => {
		if (!deleteTarget) return;
		remove.mutate(
			{ params: { path: { workspace_id: deleteTarget.id } } },
			{
				onSuccess: () => {
					toast.success("Workspace deleted");
					setDeleteTarget(null);
				},
				onError: (err) =>
					toast.error("Delete failed", {
						description: (err as Error)?.message,
					}),
			},
		);
	};

	return (
		<div className="px-6 py-8 max-w-5xl mx-auto w-full">
			<div className="mb-4">
				<Button
					variant="ghost"
					size="sm"
					className="gap-1.5 -ml-2 text-muted-foreground hover:text-foreground"
					onClick={() => navigate("/chat")}
				>
					<ArrowLeft className="h-4 w-4" />
					Back to chat
				</Button>
			</div>
			<div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
				<div>
					<h1 className="text-2xl font-semibold tracking-tight">
						Workspaces
					</h1>
					<p className="text-sm text-muted-foreground mt-1 max-w-2xl">
						Workspaces are folders for chats with shared instructions,
						tools, and knowledge. Open one to scope new chats to it.
					</p>
				</div>
				<Button className="gap-2" onClick={() => setCreateOpen(true)}>
					<Plus className="h-4 w-4" />
					New workspace
				</Button>
			</div>

			<div className="relative mb-4 max-w-md">
				<Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
				<Input
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					placeholder="Search workspaces..."
					className="pl-9"
				/>
			</div>

			{isLoading ? (
				<div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
					{[1, 2, 3, 4].map((i) => (
						<Skeleton key={i} className="h-24 w-full" />
					))}
				</div>
			) : sorted && sorted.length > 0 ? (
				<div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
					{sorted.map((ws) => (
						<WorkspaceCard
							key={ws.id}
							ws={ws}
							onOpen={() =>
								navigate(`/chat?workspace=${ws.id}`)
							}
							onEdit={() => setEditId(ws.id)}
							onDelete={() => setDeleteTarget(ws)}
							isPlatformAdmin={isPlatformAdmin}
							currentUserId={user?.id ?? null}
							currentOrgId={user?.organizationId ?? null}
						/>
					))}
				</div>
			) : (
				<div className="border-dashed border rounded-lg p-10 text-center text-sm text-muted-foreground">
					{search
						? "No workspaces match your search."
						: "You haven't created any workspaces yet."}
				</div>
			)}

			<CreateWorkspaceDialog
				open={createOpen}
				onOpenChange={setCreateOpen}
				onCreated={(ws) => navigate(`/chat?workspace=${ws.id}`)}
			/>

			{editId && (
				<EditWorkspaceLoader
					workspaceId={editId}
					onClose={() => setEditId(null)}
				/>
			)}

			<AlertDialog
				open={!!deleteTarget}
				onOpenChange={(open) => !open && setDeleteTarget(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete workspace?</AlertDialogTitle>
						<AlertDialogDescription>
							"{deleteTarget?.name}" will be hidden. Chats in this
							workspace stay in your history and can be moved to
							another workspace later.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeleteConfirm}
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

function WorkspaceCard({
	ws,
	onOpen,
	onEdit,
	onDelete,
	isPlatformAdmin,
	currentUserId,
	currentOrgId,
}: {
	ws: WorkspaceSummary;
	onOpen: () => void;
	onEdit: () => void;
	onDelete: () => void;
	isPlatformAdmin: boolean;
	currentUserId: string | null;
	currentOrgId: string | null;
}) {
	const canManage =
		isPlatformAdmin ||
		(ws.scope === "personal" && ws.user_id === currentUserId) ||
		((ws.scope === "org" || ws.scope === "role") &&
			ws.organization_id === currentOrgId);

	const ScopeIcon = ws.scope === "personal" ? Lock : Users;

	return (
		<div
			role="button"
			tabIndex={0}
			onClick={onOpen}
			onKeyDown={(e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					onOpen();
				}
			}}
			className="group text-left p-4 border rounded-lg bg-card hover:border-primary/50 hover:bg-accent/50 transition-colors flex gap-3 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
		>
			<div className="size-10 rounded-md bg-primary/10 text-primary flex items-center justify-center shrink-0">
				<FolderKanban className="h-5 w-5" />
			</div>
			<div className="flex-1 min-w-0">
				<div className="flex items-center gap-2 flex-wrap">
					<span className="font-medium truncate">{ws.name}</span>
					<Badge
						variant="secondary"
						className="text-[10px] px-1.5 py-0 gap-1"
					>
						<ScopeIcon className="size-2.5" />
						{SCOPE_LABEL[ws.scope]}
					</Badge>
				</div>
				{ws.description && (
					<p className="text-sm text-muted-foreground line-clamp-2 mt-0.5">
						{ws.description}
					</p>
				)}
				<div className="text-xs text-muted-foreground mt-1.5 flex items-center gap-1.5">
					<MessageSquare className="size-3" />
					{ws.conversation_count}{" "}
					{ws.conversation_count === 1 ? "chat" : "chats"}
				</div>
			</div>
			{canManage && (
				<div className="flex items-start gap-0.5 shrink-0 self-start">
					<Button
						variant="ghost"
						size="icon-sm"
						className="sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
						onClick={(e) => {
							e.stopPropagation();
							onEdit();
						}}
						title="Edit workspace"
					>
						<Edit3 className="h-3.5 w-3.5" />
					</Button>
					<Button
						variant="ghost"
						size="icon-sm"
						className="sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
						onClick={(e) => {
							e.stopPropagation();
							onDelete();
						}}
						title="Delete workspace"
					>
						<Trash2 className="h-3.5 w-3.5" />
					</Button>
				</div>
			)}
		</div>
	);
}

/** Loads the full workspace + opens the settings sheet for it. */
function EditWorkspaceLoader({
	workspaceId,
	onClose,
}: {
	workspaceId: string;
	onClose: () => void;
}) {
	const { isPlatformAdmin, user } = useAuth();
	const { data: ws } = useWorkspace(workspaceId);
	if (!ws) return null;

	const canManage =
		isPlatformAdmin ||
		(ws.scope === "personal" && ws.user_id === user?.id) ||
		((ws.scope === "org" || ws.scope === "role") &&
			ws.organization_id === user?.organizationId);

	return (
		<WorkspaceSettingsSheet
			workspace={ws}
			open
			onOpenChange={(open) => {
				if (!open) onClose();
			}}
			canManage={canManage}
		/>
	);
}
