/**
 * Revision history for a private Solution.
 *
 * Distinguishes Source (the current revision) from Preview (the last
 * successfully deployed revision), per the spec's revision behavior.
 */

import { useState } from "react";
import { Download, Loader2, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import type { BuilderRevision } from "@/services/builder";

export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface RevisionListProps {
	revisions: BuilderRevision[];
	isLoading: boolean;
	/** Absent when no session has started; Undo requires one. */
	canUndo: boolean;
	undoingRevisionId: string | null;
	onUndo: (revisionId: string) => void;
	onDownload: (revisionId: string) => void;
}

export function RevisionList({
	revisions,
	isLoading,
	canUndo,
	undoingRevisionId,
	onUndo,
	onDownload,
}: RevisionListProps) {
	const [pendingUndo, setPendingUndo] = useState<BuilderRevision | null>(null);

	if (isLoading) {
		return (
			<div className="space-y-2 p-4" data-testid="revisions-loading">
				<Skeleton className="h-12 w-full" />
				<Skeleton className="h-12 w-full" />
			</div>
		);
	}

	if (revisions.length === 0) {
		return (
			<p className="p-6 text-center text-sm text-muted-foreground">
				No revisions yet. Ask the builder to make a change.
			</p>
		);
	}

	return (
		<>
			<ul className="divide-y" data-testid="revision-list">
				{revisions.map((revision) => (
					<li
						key={revision.id}
						className="flex items-start justify-between gap-4 px-4 py-3"
						data-testid={`revision-${revision.id}`}
					>
						<div className="min-w-0 space-y-1">
							<div className="flex flex-wrap items-center gap-2">
								<span className="truncate text-sm font-medium">
									{revision.summary ?? "Untitled revision"}
								</span>
								{revision.is_current && <Badge>Source</Badge>}
								{revision.is_deployed && (
									<Badge variant="secondary">Preview</Badge>
								)}
								{revision.restored_from_revision_id && (
									<Badge variant="outline">Restored</Badge>
								)}
							</div>
							<p className="text-xs text-muted-foreground">
								{new Date(revision.created_at).toLocaleString()} ·{" "}
								{formatBytes(revision.size_bytes)}
							</p>
						</div>

						<div className="flex shrink-0 items-center gap-1">
							<Button
								variant="ghost"
								size="icon"
								title="Download this revision"
								aria-label={`Download revision ${revision.id}`}
								onClick={() => onDownload(revision.id)}
							>
								<Download className="h-4 w-4" />
							</Button>
							<Button
								variant="ghost"
								size="icon"
								title={
									canUndo
										? "Restore this revision"
										: "Start a builder session to restore a revision"
								}
								aria-label={`Undo to revision ${revision.id}`}
								disabled={
									!canUndo ||
									revision.is_current ||
									undoingRevisionId !== null
								}
								onClick={() => setPendingUndo(revision)}
							>
								{undoingRevisionId === revision.id ? (
									<Loader2 className="h-4 w-4 animate-spin" />
								) : (
									<Undo2 className="h-4 w-4" />
								)}
							</Button>
						</div>
					</li>
				))}
			</ul>

			<AlertDialog
				open={pendingUndo !== null}
				onOpenChange={(open) => !open && setPendingUndo(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Restore this revision?</AlertDialogTitle>
						<AlertDialogDescription>
							This adds a new revision restoring the Solution source to
							&ldquo;{pendingUndo?.summary ?? "Untitled revision"}&rdquo;.
							Later revisions stay in history.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								if (pendingUndo) onUndo(pendingUndo.id);
								setPendingUndo(null);
							}}
						>
							Restore
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
