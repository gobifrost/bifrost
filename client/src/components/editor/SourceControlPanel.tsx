import { SourceChangesSection } from "./SourceChangesSection";
import { SourceOperationDialog } from "./SourceOperationDialog";
import { SourceControlSetupState } from "./SourceControlSetupState";
import {
	SourceControlHeader,
	SourceControlMergeBanner,
} from "./SourceControlStatus";
import { CommitHistorySection } from "./CommitHistorySection";
import { useState, useEffect, useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
	useGitStatus,
	useGitCommits,
	useFetch,
	useCommit,
	useSync,
	useAbortMerge,
	useDiscard,
	useWorkingTreeChanges,
	useResolveConflicts,
	useFileDiff,
	useCleanupOrphaned,
	type ChangedFile,
	type MergeConflict,
	type EntityChange,
	type FetchResult,
	type WorkingTreeStatus,
	type CommitResult,
	type SyncResult,
	type AbortMergeResult,
	type ResolveResult,
	type DiffResult,
	type DiscardResult,
	type PreflightResult,
} from "@/hooks/useGitHub";
import { useEditorStore, type DiffPreviewState } from "@/stores/editorStore";
import {
	GitOpError,
	runGitOp as runPlatformGitOp,
	type GitOperationAccepted,
} from "./runGitOperation";

/** Log preflight validation issues to the editor terminal */
function logPreflightToTerminal(
	preflight: PreflightResult,
	commitSucceeded: boolean,
) {
	if (!preflight.issues.length) return;

	const errors = preflight.issues.filter((i) => i.severity === "error");
	const warnings = preflight.issues.filter((i) => i.severity === "warning");

	const header = commitSucceeded
		? `Commit succeeded with ${warnings.length} warning(s)`
		: `Commit blocked: ${errors.length} error(s), ${warnings.length} warning(s)`;

	const logs: Array<{
		level: string;
		message: string;
		source: string;
		timestamp: string;
	}> = [
		{
			level: commitSucceeded ? "WARNING" : "ERROR",
			message: `[Preflight] ${header}`,
			source: "preflight",
			timestamp: new Date().toISOString(),
		},
	];

	// Show errors individually, collapse warnings into a summary by category
	const hintGroups = new Map<string, number>();
	const warningsByCategory = new Map<string, number>();

	for (const issue of preflight.issues) {
		if (issue.severity === "error") {
			logs.push({
				level: "ERROR",
				message: `${issue.path}${issue.line ? ` [Line ${issue.line}]` : ""}: ${issue.message} (${issue.category})`,
				source: "preflight",
				timestamp: new Date().toISOString(),
			});
		} else {
			warningsByCategory.set(
				issue.category,
				(warningsByCategory.get(issue.category) ?? 0) + 1,
			);
		}
		if (issue.fix_hint) {
			hintGroups.set(
				issue.fix_hint,
				(hintGroups.get(issue.fix_hint) ?? 0) + 1,
			);
		}
	}

	// Collapsed warning summaries by category
	for (const [category, count] of warningsByCategory) {
		logs.push({
			level: "WARNING",
			message: `${count} ${category} warning${count !== 1 ? "s" : ""}`,
			source: "preflight",
			timestamp: new Date().toISOString(),
		});
	}

	// Append deduplicated fix hints at the end (errors only)
	for (const [hint] of hintGroups) {
		const errorHintCount = preflight.issues.filter(
			(i) => i.severity === "error" && i.fix_hint === hint,
		).length;
		if (errorHintCount > 0) {
			const suffix =
				errorHintCount > 1 ? ` (${errorHintCount} issues)` : "";
			logs.push({
				level: "INFO",
				message: `-> Fix: ${hint}${suffix}`,
				source: "preflight",
				timestamp: new Date().toISOString(),
			});
		}
	}

	useEditorStore.getState().appendTerminalOutput({
		loggerOutput: logs,
		variables: {},
		status: commitSucceeded ? "Success" : "Failed",
		executionId: `preflight-${Date.now()}`,
		error: commitSucceeded ? undefined : "Preflight validation failed",
	});
}

/** Log entity changes to the editor terminal */
function logEntityChangesToTerminal(
	changes: EntityChange[],
	context: "commit" | "sync",
) {
	if (!changes.length) return;

	const added = changes.filter((c) => c.action === "added");
	const updated = changes.filter((c) => c.action === "updated");
	const removed = changes.filter((c) => c.action === "removed");

	const countParts: string[] = [];
	if (added.length) countParts.push(`${added.length} added`);
	if (updated.length) countParts.push(`${updated.length} updated`);
	if (removed.length) countParts.push(`${removed.length} removed`);

	const label = context === "commit" ? "Commit" : "Sync";
	const header = `${label} — ${changes.length} entity change(s): ${countParts.join(", ")}`;

	const symbols = { added: "+", updated: "~", removed: "-" } as const;
	const levels = {
		added: "INFO",
		updated: "INFO",
		removed: "WARNING",
	} as const;
	const timestamp = new Date().toISOString();

	const logs: Array<{
		level: string;
		message: string;
		source: string;
		timestamp: string;
	}> = [
		{
			level: "INFO",
			message: `[Entity Changes] ${header}`,
			source: "entity-changes",
			timestamp,
		},
	];

	for (const change of changes) {
		const sym = symbols[change.action];
		const suffix = change.reason ? `  (${change.reason})` : "";
		logs.push({
			level: levels[change.action],
			message: `  ${sym} ${change.entity_type.padEnd(14)} ${change.name}${suffix}`,
			source: "entity-changes",
			timestamp,
		});
	}

	useEditorStore.getState().appendTerminalOutput({
		loggerOutput: logs,
		variables: {},
		status: "Success",
		error: undefined,
		executionId: `entity-changes-${Date.now()}`,
	});
}

/** Queue Git work through the shared durable PlatformJob transport. */
async function runGitOp<T>(
	queueFn: (jobId: string) => Promise<GitOperationAccepted>,
	resultType: string,
	onQueued?: (jobId: string) => void,
): Promise<T> {
	let executionId = `git-${resultType}-queued`;
	return runPlatformGitOp(
		queueFn,
		resultType,
		(jobId) => {
			executionId = `git-${resultType}-${jobId.slice(0, 8)}`;
			onQueued?.(jobId);
		},
		(job) => {
			const phase = job.progress.phase;
			if (!phase) return;
			const pct = job.progress.percent == null ? "" : `[${Math.round(job.progress.percent)}%] `;
			const terminal = ["succeeded", "requires_action"].includes(job.status);
			useEditorStore.getState().streamTerminalLog(
				executionId,
				{
					level: terminal ? "INFO" : job.status === "failed" ? "WARNING" : "INFO",
					message: `${pct}${phase}`,
					source: "git",
					timestamp: new Date().toISOString(),
				},
				terminal ? "Success" : job.status === "failed" ? "Failed" : "Running",
			);
		},
	);
}

/**
 * Source Control panel with GitHub Desktop semantics.
 * Independent Fetch, Pull, Push, Commit operations.
 */
export function SourceControlPanel() {
	// State
	const [commitMessage, setCommitMessage] = useState("");
	const [changedFiles, setChangedFiles] = useState<ChangedFile[]>([]);
	const [conflicts, setConflicts] = useState<MergeConflict[]>([]);
	const [conflictResolutions, setConflictResolutions] = useState<
		Record<string, "ours" | "theirs">
	>({});
	const [loading, setLoading] = useState<
		| "fetching"
		| "committing"
		| "syncing"
		| "resolving"
		| "discarding"
		| "loading_changes"
		| null
	>(null);

	const [commitsAhead, setCommitsAhead] = useState(0);
	const [commitsBehind, setCommitsBehind] = useState(0);
	const [needsSync, setNeedsSync] = useState(false);
	const [commits, setCommits] = useState<
		Array<{
			sha: string;
			message: string;
			author: string;
			timestamp: string;
			is_pushed: boolean;
		}>
	>([]);
	const [totalCommits, setTotalCommits] = useState(0);
	const [hasMoreCommits, setHasMoreCommits] = useState(false);
	const [showCleanupPrompt, setShowCleanupPrompt] = useState(false);
	const [orphanedCount, setOrphanedCount] = useState(0);
	const [pendingDeletes, setPendingDeletes] = useState<EntityChange[]>([]);

	const sidebarPanel = useEditorStore((state) => state.sidebarPanel);
	const setDiffPreview = useEditorStore((state) => state.setDiffPreview);
	const queryClient = useQueryClient();
	const diffCacheRef = useRef<Map<string, DiffResult>>(new Map());

	// Query hooks
	const {
		data: status,
		isLoading,
		isError: statusError,
		isFetching: statusFetching,
		refetch: refetchGitStatus,
	} = useGitStatus();
	const {
		data: commitsData,
		isFetching: isLoadingCommits,
		isError: commitsError,
		refetch: refetchCommits,
	} = useGitCommits(20, 0);

	// Operation hooks
	const fetchOp = useFetch();
	const commitOp = useCommit();
	const syncOp = useSync();
	const abortMergeOp = useAbortMerge();
	const changesOp = useWorkingTreeChanges();
	const resolveOp = useResolveConflicts();
	const diffOp = useFileDiff();
	const discardOp = useDiscard();
	const cleanupOp = useCleanupOrphaned();

	// Update commits state when data loads. Adjust during render with a
	// previous-reference sentinel rather than via setState-in-effect.
	const [prevCommitsDataRef, setPrevCommitsDataRef] =
		useState<typeof commitsData>(undefined);
	if (commitsData && prevCommitsDataRef !== commitsData) {
		setPrevCommitsDataRef(commitsData);
		setCommits(commitsData.commits || []);
		setTotalCommits(commitsData.total_commits);
		setHasMoreCommits(commitsData.has_more);
	}

	// Refresh helpers
	const refreshStatus = useCallback(() => {
		queryClient.invalidateQueries({
			queryKey: ["get", "/api/github/status"],
		});
		queryClient.invalidateQueries({
			queryKey: ["get", "/api/github/commits"],
		});
	}, [queryClient]);

	const [changesError, setChangesError] = useState(false);
	const loadChanges = useCallback(async () => {
		setChangesError(false);
		setLoading("loading_changes");
		try {
			const result = await runGitOp<WorkingTreeStatus>(
				(jobId) => changesOp.mutateAsync(jobId),
				"status",
			);
			setChangedFiles(result.changed_files || []);
			// Surface conflicts from real git state (or clear if resolved)
			setConflicts(result.conflicts ?? []);
			// Update ahead/behind from real git status
			setCommitsAhead(result.commits_ahead);
			setCommitsBehind(result.commits_behind);
			return true;
		} catch (error) {
			console.error("Failed to load changes:", error);
			setChangesError(true);
			return false;
		} finally {
			setLoading(null);
		}
	}, [changesOp]);

	// Load real git status (ahead/behind/conflicts) when panel mounts
	// The lightweight /status endpoint only provides initialized/configured/branch — not real git state
	const hasLoadedRef = useRef(false);
	useEffect(() => {
		if (status?.initialized && !hasLoadedRef.current) {
			hasLoadedRef.current = true;
			loadChanges();
		}
	}, [status?.initialized, loadChanges]);

	// Clear diff cache when changed files list changes (after fetch, commit, pull, discard)
	useEffect(() => {
		diffCacheRef.current.clear();
	}, [changedFiles]);

	// --- Operations ---

	const handleFetch = useCallback(async () => {
		setLoading("fetching");
		try {
			const result = await runGitOp<FetchResult>(
				(jobId) => fetchOp.mutateAsync(jobId),
				"fetch",
			);

			if (result.commits_behind > 0) setNeedsSync(true);
			// Refresh initialization/branch state as well as working changes.
			refreshStatus();
			const refreshed = await loadChanges();
			if (!refreshed) {
				toast.error(
					"Repository fetched, but working changes could not be refreshed.",
				);
			} else {
				toast.success(
					result.commits_behind > 0 || result.commits_ahead > 0
						? `${result.commits_behind} behind, ${result.commits_ahead} ahead`
						: "Already up to date",
				);
			}
		} catch (error) {
			const msg = error instanceof Error ? error.message : String(error);
			toast.error(`Fetch failed: ${msg}`);
		} finally {
			setLoading(null);
		}
	}, [fetchOp, loadChanges, refreshStatus]);

	const handleCommit = useCallback(async () => {
		if (!commitMessage.trim()) {
			toast.error("Please enter a commit message");
			return;
		}
		setLoading("committing");
		setShowCleanupPrompt(false);
		try {
			const result = await runGitOp<CommitResult>(
				(jobId) => commitOp.mutateAsync(commitMessage.trim(), jobId),
				"commit",
			);
			if (result.success) {
				toast.success(`Committed ${result.files_committed} file(s)`);
				setCommitMessage("");
				setChangedFiles([]);
				await loadChanges();
				refreshStatus();
				if (result.preflight?.issues?.length) {
					logPreflightToTerminal(result.preflight, true);
				}
				if (result.entity_changes?.length) {
					logEntityChangesToTerminal(result.entity_changes, "commit");
				}
			} else {
				toast.error(result.error || "Commit failed");
			}
		} catch (error) {
			const msg = error instanceof Error ? error.message : String(error);
			toast.error(`Commit failed: ${msg}`);
			if (error instanceof GitOpError && error.data) {
				const commitData = error.data as unknown as CommitResult;
				if (commitData.preflight?.issues?.length) {
					logPreflightToTerminal(commitData.preflight, false);
					// Check for auto-fixable issues and show cleanup prompt
					const fixableCount = commitData.preflight.issues.filter(
						(i) => i.auto_fixable,
					).length;
					if (fixableCount > 0) {
						setShowCleanupPrompt(true);
						setOrphanedCount(fixableCount);
					}
				}
			}
		} finally {
			setLoading(null);
		}
	}, [commitMessage, commitOp, loadChanges, refreshStatus]);

	const handleCleanupAndRetry = useCallback(async () => {
		setLoading("committing");
		try {
			const result = await cleanupOp.mutateAsync({});
			const cleaned = result.cleaned ?? [];
			const count = result.count ?? 0;

			// Log cleanup results to terminal
			const logs = [
				{
					level: "INFO",
					message: `[Cleanup] Removed ${count} orphaned reference(s)`,
					source: "preflight",
					timestamp: new Date().toISOString(),
				},
				...cleaned.map(
					(e: {
						entity_type: string;
						entity_name: string;
						path: string;
					}) => ({
						level: "INFO",
						message: `   Deactivated ${e.entity_type}: ${e.entity_name} (${e.path})`,
						source: "preflight",
						timestamp: new Date().toISOString(),
					}),
				),
			];
			useEditorStore.getState().appendTerminalOutput({
				loggerOutput: logs,
				variables: {},
				status: "Success",
				error: undefined,
				executionId: `cleanup-${Date.now()}`,
			});

			setShowCleanupPrompt(false);
			setOrphanedCount(0);

			// Re-commit automatically
			toast.success(
				`Cleaned ${count} orphaned reference(s), retrying commit...`,
			);
		} catch (error) {
			const msg = error instanceof Error ? error.message : String(error);
			toast.error(`Cleanup failed: ${msg}`);
			setLoading(null);
			return;
		}

		// Retry the commit
		try {
			const result = await runGitOp<CommitResult>(
				(jobId) => commitOp.mutateAsync(commitMessage.trim(), jobId),
				"commit",
			);
			if (result.success) {
				toast.success(`Committed ${result.files_committed} file(s)`);
				setCommitMessage("");
				setChangedFiles([]);
				await loadChanges();
				refreshStatus();
				if (result.preflight?.issues?.length) {
					logPreflightToTerminal(result.preflight, true);
				}
			} else {
				toast.error(result.error || "Commit failed after cleanup");
			}
		} catch (error) {
			const msg = error instanceof Error ? error.message : String(error);
			toast.error(`Commit failed after cleanup: ${msg}`);
			if (error instanceof GitOpError && error.data) {
				const commitData = error.data as unknown as CommitResult;
				if (commitData.preflight?.issues?.length) {
					logPreflightToTerminal(commitData.preflight, false);
				}
			}
		} finally {
			setLoading(null);
		}
	}, [cleanupOp, commitOp, commitMessage, loadChanges, refreshStatus]);

	const [syncError, setSyncError] = useState<string | null>(null);
	const [retryJobId, setRetryJobId] = useState<string | null>(null);
	const handleSync = useCallback(
		async (confirmDeletes = false, retryJobId?: string) => {
			setSyncError(null);
			if (!retryJobId) setRetryJobId(null);
			setLoading("syncing");
			let queuedJobId: string | null = null;
			try {
				const result = await runGitOp<SyncResult>(
					(jobId) =>
						syncOp.mutateAsync(
							jobId,
							confirmDeletes || retryJobId
								? { confirm_deletes: confirmDeletes, retry_job_id: retryJobId }
								: undefined,
						),
					"sync",
					(jobId) => { queuedJobId = jobId; },
				);
				if (
					result.needs_delete_confirmation &&
					result.pending_deletes?.length
				) {
					setPendingDeletes(result.pending_deletes);
					toast.warning(
						`${result.pending_deletes.length} entity deletion(s) require confirmation`,
					);
				} else if (result.success) {
					setRetryJobId(null);
					const parts = [];
					if (result.pushed_commits > 0)
						parts.push(`pushed ${result.pushed_commits} commit(s)`);
					if (result.entities_imported > 0)
						parts.push(
							`imported ${result.entities_imported} entities`,
						);
					const deletedCount =
						result.entity_changes?.filter(
							(entity) => entity.action === "removed",
						).length ?? 0;
					if (deletedCount > 0)
						parts.push(
							`deleted ${deletedCount} ${deletedCount === 1 ? "entity" : "entities"}`,
						);
					toast.success(
						parts.length > 0
							? `Sync complete: ${parts.join(", ")}`
							: "Sync complete",
					);
					setNeedsSync(false);
					setConflicts([]);
					setConflictResolutions({});
					setPendingDeletes([]);
					refreshStatus();
					await loadChanges();
					if (result.entity_changes?.length) {
						logEntityChangesToTerminal(
							result.entity_changes,
							"sync",
						);
					}
				} else if (result.conflicts && result.conflicts.length > 0) {
					setConflicts(result.conflicts);
					toast.warning(
						`${result.conflicts.length} conflict(s) need resolution`,
					);
				} else {
					if (result.retryable && queuedJobId) setRetryJobId(queuedJobId);
					setSyncError(result.error || "Sync failed. Try again.");
					toast.error(result.error || "Sync failed");
				}
			} catch (error) {
				// Check if this is a conflict or delete-confirmation result
				if (error instanceof GitOpError && error.data) {
					const syncData = error.data as unknown as SyncResult;
					if (
						syncData.needs_delete_confirmation &&
						syncData.pending_deletes?.length
					) {
						setPendingDeletes(syncData.pending_deletes);
						toast.warning(
							`${syncData.pending_deletes.length} entity deletion(s) require confirmation`,
						);
						return;
					}
					if (syncData.conflicts && syncData.conflicts.length > 0) {
						setConflicts(syncData.conflicts);
						toast.warning(
							`${syncData.conflicts.length} conflict(s) need resolution`,
						);
						return;
					}
					if (syncData.retryable && queuedJobId) setRetryJobId(queuedJobId);
				}
				const msg =
					error instanceof Error ? error.message : String(error);
				setSyncError(msg);
				toast.error(`Sync failed: ${msg}`);
			} finally {
				setLoading(null);
			}
		},
		[syncOp, refreshStatus, loadChanges],
	);

	const [showAbortConfirm, setShowAbortConfirm] = useState(false);
	const panelRef = useRef<HTMLDivElement>(null);
	const handleAbortMerge = useCallback(async () => {
		setLoading("resolving");
		try {
			const result = await runGitOp<AbortMergeResult>(
				(jobId) => abortMergeOp.mutateAsync(jobId),
				"abort_merge",
			);
			if (result.success) {
				toast.success("Merge aborted");
				setConflicts([]);
				setConflictResolutions({});
				refreshStatus();
				await loadChanges();
			} else {
				throw new Error(
					result.error || "Couldn’t abort the merge. Try again.",
				);
			}
		} finally {
			setLoading(null);
		}
	}, [abortMergeOp, refreshStatus, loadChanges]);

	const handleResolveConflicts = useCallback(async () => {
		const unresolvedCount = conflicts.filter(
			(c) => !conflictResolutions[c.path],
		).length;
		if (unresolvedCount > 0) {
			toast.error("Please resolve all conflicts before completing merge");
			return;
		}
		setLoading("resolving");
		try {
			const result = await runGitOp<ResolveResult>(
				(jobId) => resolveOp.mutateAsync(conflictResolutions, jobId),
				"resolve",
			);
			if (result.success) {
				toast.success("Merge complete — push to sync changes");
				setConflicts([]);
				setConflictResolutions({});
				setCommitsAhead(result.commits_ahead ?? 0);
				setCommitsBehind(result.commits_behind ?? 0);
				refreshStatus();
				await loadChanges();
			} else {
				toast.error(result.error || "Resolve failed");
			}
		} catch (error) {
			const msg = error instanceof Error ? error.message : String(error);
			toast.error(`Resolve failed: ${msg}`);
		} finally {
			setLoading(null);
		}
	}, [conflicts, conflictResolutions, resolveOp, refreshStatus, loadChanges]);

	const handleShowDiff = useCallback(
		async function showDiff(file: ChangedFile) {
			// Check cache first
			const cached = diffCacheRef.current.get(file.path);
			if (cached) {
				setDiffPreview({
					path: file.path,
					displayName: file.display_name || file.path,
					entityType: file.entity_type || "workflow",
					localContent: cached.working_content ?? null,
					remoteContent: cached.head_content ?? null,
					isConflict: false,
					isLoading: false,
				});
				return;
			}

			const pendingPreview: DiffPreviewState = {
				path: file.path,
				displayName: file.display_name || file.path,
				entityType: file.entity_type || "workflow",
				localContent: null,
				remoteContent: null,
				isConflict: false,
				isLoading: true,
			};
			setDiffPreview(pendingPreview);

			try {
				const result = await runGitOp<DiffResult>(
					(jobId) => diffOp.mutateAsync(file.path, jobId),
					"diff",
				);
				if (useEditorStore.getState().diffPreview !== pendingPreview)
					return;
				// Store in cache
				diffCacheRef.current.set(file.path, result);
				setDiffPreview({
					path: file.path,
					displayName: file.display_name || file.path,
					entityType: file.entity_type || "workflow",
					localContent: result.working_content ?? null,
					remoteContent: result.head_content ?? null,
					isConflict: false,
					isLoading: false,
				});
			} catch (error) {
				if (useEditorStore.getState().diffPreview !== pendingPreview)
					return;
				setDiffPreview({
					...pendingPreview,
					isLoading: false,
					error:
						error instanceof Error
							? error.message
							: "Couldn’t load this comparison.",
					onRetry: () => void showDiff(file),
				});
			}
		},
		[diffOp, setDiffPreview],
	);

	const handleShowConflictDiff = useCallback(
		(conflict: MergeConflict) => {
			const resolution = conflictResolutions[conflict.path];
			setDiffPreview({
				path: conflict.path,
				displayName: conflict.display_name || conflict.path,
				entityType: conflict.entity_type || "workflow",
				localContent: conflict.ours_content ?? null,
				remoteContent: conflict.theirs_content ?? null,
				isConflict: true,
				isLoading: false,
				resolution,
				conflictType: conflict.conflict_type,
				onResolve: (res) => {
					setConflictResolutions((prev) => ({
						...prev,
						[conflict.path]: res,
					}));
					// Update diff preview resolution
					setDiffPreview((prev) =>
						prev ? { ...prev, resolution: res } : null,
					);
				},
			});
		},
		[conflictResolutions, setDiffPreview],
	);

	const handleDiscardFiles = useCallback(
		async (files: ChangedFile[]) => {
			if (files.length === 0) return;
			setLoading("discarding");
			try {
				const result = await runGitOp<DiscardResult>(
					(jobId) =>
						discardOp.mutateAsync(
							files.map((file) => file.path),
							jobId,
						),
					"discard",
				);
				if (!result.success)
					throw new Error(
						result.error || "Couldn’t discard changes. Try again.",
					);
				toast.success(
					`Discarded changes to ${files.length} ${files.length === 1 ? "file" : "files"}`,
				);
				const discardedPaths = new Set(files.map((file) => file.path));
				setChangedFiles((current) =>
					current.filter((file) => !discardedPaths.has(file.path)),
				);
				setNeedsSync(true);
				await loadChanges();
				refreshStatus();
			} finally {
				setLoading(null);
			}
		},
		[discardOp, loadChanges, refreshStatus],
	);

	// Auto-refresh on visibility change
	useEffect(() => {
		if (sidebarPanel !== "sourceControl") return;

		const handleVisibility = () => {
			if (!document.hidden) {
				refreshStatus();
				loadChanges();
			}
		};
		document.addEventListener("visibilitychange", handleVisibility);
		return () =>
			document.removeEventListener("visibilitychange", handleVisibility);
	}, [sidebarPanel, refreshStatus, loadChanges]);

	// --- Render ---

	if (!status && statusError)
		return (
			<SourceControlSetupState
				state="error"
				busy={statusFetching}
				onAction={() => void refetchGitStatus()}
			/>
		);
	if (isLoading || !status)
		return <SourceControlSetupState state="loading" />;
	if (!status.initialized)
		return (
			<SourceControlSetupState
				state={status.configured ? "initialize" : "configure"}
				busy={!!loading}
				onAction={handleFetch}
			/>
		);

	const hasConflicts = conflicts.length > 0;
	const resolvedCount = Object.keys(conflictResolutions).length;
	const allConflictsResolved =
		hasConflicts && resolvedCount === conflicts.length;

	return (
		<div ref={panelRef} className="flex h-full flex-col">
			<SourceControlHeader
				branch={status.current_branch}
				isFetching={loading === "fetching"}
				disabled={!!loading}
				onFetch={handleFetch}
			/>

			{statusError && (
				<div className="space-y-2 border-b p-3">
					<p role="alert" className="text-sm text-destructive">
						Couldn’t refresh Git status. Previously loaded
						information is shown.
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={statusFetching}
						onClick={() => void refetchGitStatus()}
					>
						Retry Git status
					</Button>
				</div>
			)}

			{showAbortConfirm && (
				<SourceOperationDialog
					title="Abort merge?"
					description="Return the repository to its state before the pull. Conflict resolution work for this merge will be discarded."
					confirmLabel="Abort merge"
					pendingLabel="Aborting merge…"
					cancelLabel="Keep reviewing"
					onConfirm={handleAbortMerge}
					onClose={() => {
						setShowAbortConfirm(false);
						requestAnimationFrame(() => {
							const panel = panelRef.current;
							const button = Array.from(
								panel?.querySelectorAll("button") ?? [],
							).find(
								(button) =>
									button.textContent?.trim() ===
									"Abort merge",
							);
							(
								button ??
								panel?.querySelector<HTMLButtonElement>(
									'[aria-label="Fetch from remote"]',
								)
							)?.focus();
						});
					}}
				/>
			)}

			{/* Scrollable sections */}
			<div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
				{/* Merge banner + unified list when conflicts exist */}
				{hasConflicts && (
					<SourceControlMergeBanner
						conflictCount={conflicts.length}
						resolvedCount={resolvedCount}
						onAbortMerge={() => setShowAbortConfirm(true)}
						disabled={!!loading}
					/>
				)}

				{/* Changes (uncommitted) — includes conflicts in unified list when merging */}
				<SourceChangesSection
					syncError={syncError}
					hasLoadError={changesError}
					onRetryLoad={loadChanges}
					changedFiles={changedFiles}
					conflicts={hasConflicts ? conflicts : []}
					conflictResolutions={conflictResolutions}
					onShowConflictDiff={handleShowConflictDiff}
					onResolveConflict={(path, res) => {
						setConflictResolutions((prev) => ({
							...prev,
							[path]: res,
						}));
						setDiffPreview((prev) =>
							prev?.path === path
								? { ...prev, resolution: res }
								: prev,
						);
					}}
					commitMessage={commitMessage}
					onCommitMessageChange={setCommitMessage}
					onCommit={handleCommit}
					onCompleteMerge={handleResolveConflicts}
					allConflictsResolved={allConflictsResolved}
					onSync={handleSync}
					onShowDiff={handleShowDiff}
					onDiscardFiles={handleDiscardFiles}
					commitsBehind={commitsBehind}
					commitsAhead={commitsAhead}
					needsSync={needsSync}
					loading={loading}

					disabled={!!loading}
					branch={status.current_branch || "main"}
					showCleanupPrompt={showCleanupPrompt}
					orphanedCount={orphanedCount}
					onCleanupAndRetry={handleCleanupAndRetry}
					onDismissCleanup={() => setShowCleanupPrompt(false)}
					pendingDeletes={pendingDeletes}
					onRetryPublication={retryJobId ? () => handleSync(false, retryJobId) : undefined}
					onConfirmDeletes={() => handleSync(true)}
					onDismissDeletes={() => setPendingDeletes([])}
				/>

				{/* Commits */}
				<CommitHistorySection
					commits={commits}
					totalCommits={totalCommits}
					hasMore={hasMoreCommits}
					isLoading={isLoadingCommits}
					hasError={commitsError}
					onRetry={() => void refetchCommits()}
				/>
			</div>
		</div>
	);
}

// =============================================================================
// Sub-components
// =============================================================================
