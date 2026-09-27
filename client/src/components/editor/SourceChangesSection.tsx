import { useId, useRef, useState } from "react";
import { Loader2, ChevronDown, ChevronRight, Edit3, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { cn } from "@/lib/utils";
import type {
	ChangedFile,
	MergeConflict,
	EntityChange,
} from "@/hooks/useGitHub";
import {
	SourceCleanupPrompt,
	SourceDeletionPrompt,
} from "./SourceControlPrompts";
import { SourceControlActions } from "./SourceControlActions";
import {
	ChangedFileRecord,
	ConflictFileRecord,
} from "./SourceControlFileRecords";
import { SourceDiscardDialog } from "./SourceDiscardDialog";
export function SourceChangesSection({
	syncError,
	hasLoadError,
	onRetryLoad,
	changedFiles,
	conflicts,
	conflictResolutions,
	onShowConflictDiff,
	onResolveConflict,
	commitMessage,
	onCommitMessageChange,
	onCommit,
	onCompleteMerge,
	allConflictsResolved,
	onSync,
	onShowDiff,
	onDiscardFiles,
	commitsBehind,
	commitsAhead,
	needsSync,
	loading,
	disabled,
	branch,
	showCleanupPrompt,
	orphanedCount,
	onCleanupAndRetry,
	onDismissCleanup,
	pendingDeletes,
	onConfirmDeletes,
	onDismissDeletes,
	onRetryPublication,
}: {
	syncError: string | null;
	hasLoadError: boolean;
	onRetryLoad: () => void;
	changedFiles: ChangedFile[];
	conflicts: MergeConflict[];
	conflictResolutions: Record<string, "ours" | "theirs">;
	onShowConflictDiff: (conflict: MergeConflict) => void;
	onResolveConflict: (path: string, resolution: "ours" | "theirs") => void;
	commitMessage: string;
	onCommitMessageChange: (msg: string) => void;
	onCommit: () => void;
	onCompleteMerge: () => void;
	allConflictsResolved: boolean;
	onSync: (confirmDeletes?: boolean) => void;
	onShowDiff: (file: ChangedFile) => void;
	onDiscardFiles: (files: ChangedFile[]) => Promise<void>;
	commitsBehind: number;
	commitsAhead: number;
	needsSync: boolean;
	loading:
		| "fetching"
		| "committing"
		| "syncing"
		| "resolving"
		| "discarding"
		| "loading_changes"
		| null;
	disabled: boolean;
	branch: string;
	showCleanupPrompt?: boolean;
	orphanedCount?: number;
	onCleanupAndRetry?: () => void;
	onDismissCleanup?: () => void;
	pendingDeletes?: EntityChange[];
	onConfirmDeletes?: () => void;
	onDismissDeletes?: () => void;
	onRetryPublication?: () => void;
}) {
	const writesDisabled = disabled || hasLoadError;
	const unavailableReason = hasLoadError
		? "Refresh working changes before making repository changes."
		: disabled
			? "Wait for the current repository operation to finish."
			: undefined;
	const contentId = useId();
	const [expanded, setExpanded] = useState(true);
	const discardTriggerRef = useRef<HTMLElement | null>(null);
	const changesTriggerRef = useRef<HTMLButtonElement>(null);
	const [discardFiles, setDiscardFiles] = useState<ChangedFile[] | null>(
		null,
	);
	const openDiscard = (files: ChangedFile[]) => {
		discardTriggerRef.current =
			document.activeElement instanceof HTMLElement
				? document.activeElement
				: null;
		setDiscardFiles(files);
	};

	const hasConflicts = conflicts.length > 0;
	const hasChanges = changedFiles.length > 0;
	const totalItems = conflicts.length + changedFiles.length;

	return (
		<div
			className={cn(
				"border-t flex flex-col min-h-0",
				expanded && "flex-1",
			)}
		>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<button
						ref={changesTriggerRef}
						type="button"
						aria-expanded={expanded}
						aria-controls={contentId}
						onClick={() => setExpanded(!expanded)}
						className="min-h-11 w-full px-4 py-2 flex items-center gap-2 hover:bg-muted/30 transition-colors text-left flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none"
					>
						{expanded ? (
							<ChevronDown className="h-4 w-4 flex-shrink-0" />
						) : (
							<ChevronRight className="h-4 w-4 flex-shrink-0" />
						)}
						<Edit3 className="h-4 w-4 flex-shrink-0" />
						<span className="text-sm font-medium flex-1 truncate">
							Changes
						</span>
						<span className="text-xs text-muted-foreground bg-muted w-10 text-center py-0.5 rounded-full flex-shrink-0">
							{hasLoadError && totalItems === 0
								? "—"
								: totalItems}
						</span>
					</button>
				</ContextMenuTrigger>
				<ContextMenuContent className="z-[200]">
					<ContextMenuItem
						disabled={!hasChanges || hasConflicts || writesDisabled}
						onClick={() => openDiscard([...changedFiles])}
					>
						<Undo2 className="h-4 w-4 mr-2" />
						Discard All Changes
					</ContextMenuItem>
				</ContextMenuContent>
			</ContextMenu>
			{expanded && (
				<div
					id={contentId}
					className="flex-1 flex flex-col overflow-hidden min-h-0"
				>
					{hasLoadError && (
						<div className="space-y-2 border-b p-3">
							<p
								role="alert"
								className="text-sm text-destructive"
							>
								Couldn’t load working changes. Refresh before
								making repository changes.
								{totalItems > 0 &&
									" Previously loaded files are shown below."}
							</p>
							<Button
								variant="outline"
								className="min-h-11"
								onClick={onRetryLoad}
								disabled={disabled}
							>
								Retry loading changes
							</Button>
						</div>
					)}
					<SourceControlActions
						hasChanges={hasChanges}
						hasConflicts={hasConflicts}
						allConflictsResolved={allConflictsResolved}
						commitMessage={commitMessage}
						onCommitMessageChange={onCommitMessageChange}
						onCommit={onCommit}
						onCompleteMerge={onCompleteMerge}
						onSync={() => onSync()}
						commitsAhead={commitsAhead}
						commitsBehind={commitsBehind}
						needsSync={needsSync}
						disabled={writesDisabled}
						loading={loading}
						branch={branch}
					/>

					{hasChanges && !hasConflicts && (
						<Button
							type="button"
							variant="ghost"
							className="mx-3 mb-3 min-h-11 h-auto whitespace-normal self-start"
							disabled={writesDisabled}
							onClick={() => openDiscard([...changedFiles])}
						>
							<Undo2 className="size-4" />
							Discard all changes
						</Button>
					)}

					{syncError && !pendingDeletes?.length && (
						<div className="mx-3 mb-3 space-y-2">
							<p
								role="alert"
								className="text-sm text-destructive [overflow-wrap:anywhere]"
							>
								Sync failed: {syncError}
							</p>
							{onRetryPublication && (
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									disabled={disabled}
									onClick={onRetryPublication}
								>
									Retry publication
								</Button>
							)}
						</div>
					)}
					{showCleanupPrompt && (
						<SourceCleanupPrompt
							count={orphanedCount ?? 0}
							disabled={disabled}
							isPending={loading === "committing"}
							onConfirm={
								hasLoadError ? undefined : onCleanupAndRetry
							}
							onDismiss={onDismissCleanup}
						/>
					)}
					{pendingDeletes && pendingDeletes.length > 0 && (
						<SourceDeletionPrompt
							error={syncError}
							entities={pendingDeletes}
							disabled={disabled}
							isPending={loading === "syncing"}
							onConfirm={
								hasLoadError ? undefined : onConfirmDeletes
							}
							onDismiss={onDismissDeletes}
						/>
					)}

					{/* File list */}
					<div className="flex-1 overflow-y-auto px-4 pb-2 min-h-0">
						{loading === "loading_changes" && (
							<p
								role="status"
								className="flex items-center gap-2 py-3 text-sm text-muted-foreground"
							>
								<Loader2 className="size-4 shrink-0 animate-spin motion-reduce:animate-none" />
								Loading changes…
							</p>
						)}
						{!hasLoadError &&
							loading !== "loading_changes" &&
							totalItems === 0 && (
								<p className="py-3 text-sm text-muted-foreground">
									No uncommitted changes
								</p>
							)}
						{totalItems > 0 && (
							<>
								{conflicts.map((conflict) => (
									<ConflictFileRecord
										key={conflict.path}
										conflict={conflict}
										resolution={
											conflictResolutions[conflict.path]
										}
										disabled={disabled}
										onShowDiff={() =>
											onShowConflictDiff(conflict)
										}
										onResolve={(resolution) =>
											onResolveConflict(
												conflict.path,
												resolution,
											)
										}
									/>
								))}
								{changedFiles.map((file) => (
									<ChangedFileRecord
										discardDisabled={hasLoadError}
										key={file.path}
										file={file}
										disabled={disabled}
										onShowDiff={() => onShowDiff(file)}
										onDiscard={
											hasConflicts
												? undefined
												: () => openDiscard([file])
										}
									/>
								))}
							</>
						)}
					</div>
				</div>
			)}

			{discardFiles && (
				<SourceDiscardDialog
					unavailableReason={unavailableReason}
					files={discardFiles}
					onClose={() => {
						setDiscardFiles(null);
						requestAnimationFrame(() => {
							const origin = discardTriggerRef.current;
							if (
								origin?.isConnected &&
								!origin.matches(":disabled")
							)
								origin.focus();
							else changesTriggerRef.current?.focus();
						});
					}}
					onConfirm={onDiscardFiles}
				/>
			)}
		</div>
	);
}
