import { ExecutionActivityFeed } from "@/components/execution/ExecutionActivityFeed";
import "@/components/execution/execution-details.css";
import { ExecutionInspector } from "@/components/execution/ExecutionInspector";
import { RunDetailHeading } from "@/components/execution/RunDetailHeading";
import { ExecutionPageHeader } from "@/components/execution/ExecutionPageHeader";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { XCircle, Loader2, RefreshCw, ChevronDown, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ExecutionReadError } from "@/components/execution/ExecutionReadError";
import { PageLoader } from "@/components/PageLoader";
import { useExecution, cancelExecution } from "@/hooks/useExecutions";
import { useAuth } from "@/contexts/AuthContext";
import { executeWorkflowWithContext } from "@/hooks/useWorkflows";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { useEditorStore } from "@/stores/editorStore";
import { fileService } from "@/services/fileService";
import { toast } from "sonner";
import { useExecutionStream } from "@/hooks/useExecutionStream";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";
import {
	ExecutionResultPanel,
	ExecutionLogsPanel,
	ExecutionSidebar,
	ExecutionCancelDialog,
	ExecutionRerunDialog,
	ExecutionActivityTrace,
	RunStatusBadge,
	PrettyInputDisplay,
	type LogEntry,
} from "@/components/execution";
import { Skeleton } from "@/components/ui/skeleton";
import type { components } from "@/lib/v1";
import {
	mergeLogsWithDedup,
	type ExecutionLogEntry,
} from "@/lib/executionLogs";
import { useQueryClient } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { copyToClipboard } from "@/lib/clipboard";
import type { StreamingLog } from "@/stores/executionStreamStore";
import {
	createExecutionHistoryRestoreState,
	readExecutionHistoryOrigin,
} from "./ExecutionHistory/navigation";

type ExecutionStatus =
	components["schemas"]["ExecutionStatus"] | "Cancelling" | "Cancelled";
type WorkflowExecution = components["schemas"]["WorkflowExecution"];
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type FileMetadata = components["schemas"]["FileMetadata"];
type WorkflowExecutionResponse =
	components["schemas"]["WorkflowExecutionResponse"];

// Type for metadata response from useWorkflowsMetadata hook
interface WorkflowsMetadataResponse {
	workflows: WorkflowMetadata[];
	dataProviders: unknown[];
}

// Stable empty array so the merged-logs memo doesn't recompute every
// render while no stream is attached.
const NO_STREAMING_LOGS: StreamingLog[] = [];
const TERMINAL_EXECUTION_STATUSES = new Set<ExecutionStatus>([
	"Success",
	"Failed",
	"CompletedWithErrors",
	"Timeout",
	"Cancelled",
]);

function isTerminalExecutionStatus(status: unknown): status is ExecutionStatus {
	return (
		typeof status === "string" &&
		TERMINAL_EXECUTION_STATUSES.has(status as ExecutionStatus)
	);
}

/** Copy with the secure/insecure-context-aware helper; only toast success when it worked. */
async function copyWithToast(text: string, successMessage: string) {
	if (await copyToClipboard(text)) {
		toast.success(successMessage);
	} else {
		toast.error("Failed to copy to clipboard");
	}
}

interface ExecutionDetailsProps {
	/** Execution ID - if not provided, uses URL param */
	executionId?: string;
	/** Embedded mode - hides navigation header for use in panels */
	embedded?: boolean;
	/** DOM element where action buttons should be portaled (embedded mode).
	 * Pass via callback ref / state to keep this in render-friendly form. */
	actionsContainer?: HTMLDivElement | null;
	/** Called when a rerun creates a new execution (embedded mode switches to it instead of navigating) */
	onExecutionChange?: (newExecutionId: string) => void;
}

export function ExecutionDetails({
	executionId: propExecutionId,
	embedded = false,
	actionsContainer,
	onExecutionChange,
}: ExecutionDetailsProps) {
	const { executionId: urlExecutionId } = useParams();
	const executionId = propExecutionId || urlExecutionId;
	const navigate = useNavigate();
	const location = useLocation();
	const [historyOrigin] = useState(() =>
		readExecutionHistoryOrigin(location.state),
	);
	const [usageReturn] = useState(() => {
		const candidate = (location.state as { usageReturn?: unknown } | null)
			?.usageReturn;
		if (typeof candidate !== "string") return null;
		try {
			const url = new URL(candidate, window.location.origin);
			return url.origin === window.location.origin &&
				url.pathname === "/reports/usage"
				? `${url.pathname}${url.search}`
				: null;
		} catch {
			return null;
		}
	});
	const { isPlatformAdmin, hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");
	const queryClient = useQueryClient();
	const executionQueryKey = useMemo(
		() => [
			"get",
			"/api/executions/{execution_id}",
			{ params: { path: { execution_id: executionId } } },
		],
		[executionId],
	);
	const hasCachedExecution = !!queryClient.getQueryData(executionQueryKey);

	// Only the full page consumes execution-trigger state; embedded previews
	// share the parent page's location state (including History restoration).
	// location.state persists across browser refreshes (React Router uses history.state),
	// so we clear it immediately after reading to prevent deferred-fetch on refresh.
	const [hasNavigationState] = useState(
		() =>
			!embedded &&
			location.state != null &&
			historyOrigin === null &&
			usageReturn === null,
	);
	const shouldDeferInitialFetch = hasNavigationState && !hasCachedExecution;
	useEffect(() => {
		if (hasNavigationState && location.state != null) {
			navigate(
				{ pathname: location.pathname, search: location.search },
				{ replace: true, state: null },
			);
		}
	}, []); // eslint-disable-line react-hooks/exhaustive-deps -- clear once on mount
	const handleBackToHistory = () => {
		if (usageReturn) {
			navigate(usageReturn, { replace: true });
			return;
		}
		if (historyOrigin) {
			navigate(historyOrigin.href, {
				replace: true,
				state: createExecutionHistoryRestoreState(historyOrigin),
			});
			return;
		}
		navigate("/history");
	};

	// WebSocket streaming enabled state - starts enabled only for new executions from triggers
	const [signalrEnabled, setSignalrEnabled] = useState(false);

	// Fallback timer - enable fetch after 5s if WebSocket hasn't received updates
	const [fetchFallbackEnabled, setFetchFallbackEnabled] = useState(
		!shouldDeferInitialFetch,
	);

	// Get streaming logs from store
	// Use stable selector to avoid infinite loops
	const streamState = useExecutionStreamStore((state) =>
		executionId ? state.streams[executionId] : undefined,
	);
	const streamingLogs = streamState?.streamingLogs ?? NO_STREAMING_LOGS;

	// Reset fallback when execution ID changes (important for rerun
	// navigation). Adjust during render with a previous-ID sentinel rather
	// than a setState-in-effect cycle.
	const [prevExecutionId, setPrevExecutionId] = useState(executionId);
	if (prevExecutionId !== executionId) {
		setPrevExecutionId(executionId);
		setFetchFallbackEnabled(!shouldDeferInitialFetch);
	}

	// Fallback timer — set 5s fallback for navigation state. Timer-based
	// state transitions are a legitimate effect since they happen after a
	// scheduled callback (not synchronously in the effect body).
	useEffect(() => {
		if (shouldDeferInitialFetch) {
			const timer = setTimeout(() => setFetchFallbackEnabled(true), 5000);
			return () => clearTimeout(timer);
		}
		return undefined;
	}, [executionId, shouldDeferInitialFetch]);

	// Determine if we should fetch from API
	// Fetch when:
	// - Stream received update (confirms DB write), OR
	// - Fallback timer expired (5s after navigation), OR
	// - No navigation state (direct link/refresh - fetch immediately!)
	const hasReceivedUpdate = streamState?.hasReceivedUpdate ?? false;
	const shouldFetchExecution = hasReceivedUpdate || fetchFallbackEnabled;

	// State for confirmation dialogs
	const [showCancelDialog, setShowCancelDialog] = useState(false);
	const [isCancelling, setIsCancelling] = useState(false);
	const [cancelError, setCancelError] = useState<string>();
	const cancelBusy = useRef(false);
	const [showRerunDialog, setShowRerunDialog] = useState(false);
	const [isRerunning, setIsRerunning] = useState(false);
	const [rerunError, setRerunError] = useState<string>();
	const rerunBusy = useRef(false);
	const [isOpeningInEditor, setIsOpeningInEditor] = useState(false);
	const [selectedContentState, setSelectedContentState] = useState<{
		executionId: string | undefined;
		tab: "result" | "input" | "logs";
	}>({ executionId, tab: "result" });
	const selectedContentTab =
		selectedContentState.executionId === executionId
			? selectedContentState.tab
			: "result";
	const setSelectedContentTab = (tab: "result" | "input" | "logs") => {
		setSelectedContentState({ executionId, tab });
	};

	// Editor store actions
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const setSidebarPanel = useEditorStore((state) => state.setSidebarPanel);
	const minimizeEditor = useEditorStore((state) => state.minimizeEditor);

	// Workflow metadata is only needed for admin-only editor/rerun actions.
	const { data: metadataData } = useWorkflowsMetadata({
		enabled: isPlatformAdmin,
	});
	const metadata = metadataData as WorkflowsMetadataResponse | undefined;

	// Wrap onComplete in useCallback to prevent infinite loop
	const handleStreamComplete = useCallback(() => {
		// Refetch full execution data when complete
		queryClient.invalidateQueries({
			queryKey: executionQueryKey,
		});
	}, [queryClient, executionQueryKey]);

	// Real-time updates via WebSocket (only for running/pending/cancelling executions)
	const { isConnected } = useExecutionStream({
		executionId: executionId || "",
		enabled: !!executionId && signalrEnabled,
		onComplete: handleStreamComplete,
	});

	// Fetch execution data - deferred until stream confirms DB write or fallback expires
	const {
		data: executionData,
		isLoading,
		error,
		isFetching,
		refetch,
	} = useExecution(shouldFetchExecution ? executionId : undefined, {
		// Disable polling when WebSocket is connected AND execution is not complete
		// This prevents duplicate API calls while streaming
		disablePolling: isConnected && signalrEnabled,
	});

	// Cast execution data to the correct type
	const execution = executionData as WorkflowExecution | undefined;

	// Execution status and completion check
	const executionStatus = execution?.status as ExecutionStatus | undefined;
	const reduceMotion = useReducedMotion();
	const isCancelled = executionStatus === "Cancelled";
	const outcomeTone = isCancelled
		? "border-border bg-muted/50 text-muted-foreground"
		: "border-destructive/30 bg-destructive/10 text-destructive";
	const isComplete =
		executionStatus === "Success" ||
		executionStatus === "Failed" ||
		executionStatus === "CompletedWithErrors" ||
		executionStatus === "Timeout" ||
		executionStatus === "Cancelled";
	const isActive =
		executionStatus === "Running" ||
		executionStatus === "Pending" ||
		executionStatus === "Cancelling";

	// Data now comes from single API call - create adapter variables for compatibility
	const resultData = execution
		? { result: execution.result, result_type: execution.result_type }
		: undefined;
	const logsData = execution?.logs as ExecutionLogEntry[] | undefined;
	const variablesData = execution?.variables as
		Record<string, unknown> | undefined;

	// Loading states - all data comes at once now
	const isLoadingResult = isLoading;
	const isLoadingLogs = isLoading;

	// Drive `signalrEnabled` directly from current props/state during render.
	// Three rules, in order:
	//   1. If the stream has reported completion, force OFF (sticky).
	//   2. Otherwise, if we navigated in from a trigger, force ON.
	//   3. Otherwise, ON iff the execution looks running.
	// This avoids the race where status flips to a terminal state before
	// the stream's onComplete callback fires (we keep streaming until the
	// stream itself says it's done).
	const desiredSignalrEnabled = streamState?.isComplete
		? false
		: hasNavigationState ||
			executionStatus === "Pending" ||
			executionStatus === "Running" ||
			executionStatus === "Cancelling" ||
			signalrEnabled;
	if (desiredSignalrEnabled !== signalrEnabled) {
		setSignalrEnabled(desiredSignalrEnabled);
	}

	// Update execution status optimistically from stream
	// Only depend on status, not the entire streamState object, to avoid running
	// on every log message (which would trigger setQueryData unnecessarily)
	const streamStatus = streamState?.status;
	useEffect(() => {
		if (streamStatus && executionId) {
			// Use openapi-react-query's query key format
			queryClient.setQueryData(executionQueryKey, (old: unknown) => {
				if (!old || typeof old !== "object") return old;
				const oldStatus = (old as Record<string, unknown>).status;
				if (
					isTerminalExecutionStatus(oldStatus) &&
					!isTerminalExecutionStatus(streamStatus)
				) {
					return old;
				}
				return {
					...(old as Record<string, unknown>),
					status: streamStatus,
				};
			});
		}
	}, [streamStatus, executionId, queryClient, executionQueryKey]);

	const handleCancelExecution = async () => {
		if (!executionId || !execution || cancelBusy.current) return;
		cancelBusy.current = true;
		setIsCancelling(true);
		setCancelError(undefined);
		try {
			await cancelExecution(executionId);
			toast.success(
				`Cancellation requested for ${execution.workflow_name}`,
			);
			setShowCancelDialog(false);
			// Refetch to show updated status - use openapi-react-query's query key format
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/executions/{execution_id}",
					{ params: { path: { execution_id: executionId } } },
				],
			});
		} catch {
			setCancelError("Couldn't request cancellation. Try again.");
		} finally {
			cancelBusy.current = false;
			setIsCancelling(false);
		}
	};

	const handleRerunExecution = async () => {
		if (!execution || rerunBusy.current) return;
		setRerunError(undefined);

		// Look up the workflow ID from metadata
		const workflow = metadata?.workflows?.find(
			(w: WorkflowMetadata) => w.name === execution.workflow_name,
		);

		if (!workflow?.id) {
			setRerunError(
				"The workflow is unavailable. Close this dialog and refresh before trying again.",
			);
			return;
		}

		rerunBusy.current = true;
		setIsRerunning(true);
		try {
			const result = (await executeWorkflowWithContext(
				workflow.id,
				execution.input_data as Record<string, unknown>,
			)) as WorkflowExecutionResponse;

			toast.success(
				`Workflow ${execution.workflow_name} restarted successfully`,
			);
			setShowRerunDialog(false);

			if (result?.execution_id) {
				if (embedded && onExecutionChange) {
					onExecutionChange(result.execution_id);
				} else {
					navigate(`/history/${result.execution_id}`, {
						state: {
							workflow_name: execution.workflow_name,
							workflow_id: workflow.id,
							input_data: execution.input_data,
						},
					});
				}
			}
		} catch {
			setRerunError(
				"Couldn't start the workflow. Your original input is ready to retry.",
			);
		} finally {
			rerunBusy.current = false;
			setIsRerunning(false);
		}
	};

	const handleOpenInEditor = async () => {
		if (!execution) return;

		// Find the workflow's relative file path from metadata
		const workflow = metadata?.workflows?.find(
			(w: WorkflowMetadata) => w.name === execution.workflow_name,
		);
		const relativeFilePath = workflow?.relative_file_path;

		if (!relativeFilePath) {
			toast.error("Cannot open in editor: source file not found");
			return;
		}

		setIsOpeningInEditor(true);
		try {
			// Read the file using the relative path directly
			const fileResponse = await fileService.readFile(relativeFilePath);

			// Get file name from path
			const fileName =
				relativeFilePath.split("/").pop() || relativeFilePath;
			const extension = fileName.includes(".")
				? fileName.split(".").pop()!
				: null;

			// Create a minimal FileMetadata object for the tab
			const fileMetadata: FileMetadata = {
				name: fileName,
				path: relativeFilePath,
				type: "file",
				size: 0,
				extension,
				modified: new Date().toISOString(),
				entity_type: null,
				entity_id: null,
			};

			// Minimize the current details page
			minimizeEditor();

			// Open editor
			openEditor();

			// Open file in a new tab
			openFileInTab(
				fileMetadata,
				fileResponse.content,
				fileResponse.encoding as "utf-8" | "base64",
				fileResponse.etag,
			);

			// Switch to run panel to show the terminal
			setSidebarPanel("run");

			toast.success("Opened in editor");
		} catch (error) {
			console.error("Failed to open in editor:", error);
			toast.error("Failed to open file in editor");
		} finally {
			setIsOpeningInEditor(false);
		}
	};

	// Merged logs for the logs panel — memoized so ExecutionLogsPanel's
	// traceback-coalescing memo keeps a stable input (rebuilding this array
	// every render made coalescing O(n²) while streaming).
	const mergedLogs = useMemo(() => {
		const existingLogs = (logsData as ExecutionLogEntry[]) || [];
		if (
			executionStatus === "Running" ||
			executionStatus === "Pending" ||
			executionStatus === "Cancelling"
		) {
			return mergeLogsWithDedup(existingLogs, streamingLogs);
		}
		return existingLogs;
	}, [logsData, streamingLogs, executionStatus]);

	// Skeleton mirroring the drawer layout: identity header, a content
	// block, and log lines — holds the layout instead of collapsing to a
	// centered spinner.
	const embeddedSkeleton = (
		<div className="p-4 space-y-4" data-testid="execution-details-skeleton">
			<div className="space-y-2">
				<div className="flex items-center gap-2">
					<Skeleton className="h-5 w-48" />
					<Skeleton className="h-5 w-20 rounded-full" />
				</div>
				<Skeleton className="h-3 w-72" />
			</div>
			<Skeleton className="h-28 w-full" />
			<div className="space-y-2">
				<Skeleton className="h-3 w-full" />
				<Skeleton className="h-3 w-5/6" />
				<Skeleton className="h-3 w-4/5" />
			</div>
		</div>
	);

	// Show "waiting" state when we came from trigger and haven't received data yet
	// This happens before shouldFetchExecution becomes true (waiting for WebSocket or 5s fallback)
	if (!shouldFetchExecution && !execution) {
		if (embedded) {
			return embeddedSkeleton;
		}
		return <PageLoader message="Waiting for execution to start..." />;
	}

	// Show loading state during initial load
	if (isLoading) {
		if (embedded) {
			return embeddedSkeleton;
		}
		return <PageLoader message="Loading execution details..." />;
	}

	if (!execution) {
		return (
			<div className={embedded ? "p-4" : "mx-auto max-w-2xl p-4 sm:p-6"}>
				<ExecutionReadError
					pending={isFetching}
					onRetry={() => void refetch()}
					onBack={embedded ? undefined : handleBackToHistory}
					backLabel={
						usageReturn ? "Back to usage" : "Back to history"
					}
				/>
			</div>
		);
	}
	const refreshError = error ? (
		<ExecutionReadError
			cached
			backLabel={usageReturn ? "Back to usage" : "Back to history"}
			pending={isFetching}
			onRetry={() => void refetch()}
		/>
	) : null;

	const runningResult = (
		<div className="space-y-4">
			{(mergedLogs.length === 0 ||
				executionStatus === "Pending" ||
				executionStatus === "Cancelling") && (
				<p className="text-sm text-muted-foreground">
					{executionStatus === "Pending"
						? "This run is waiting to start. The result will appear here when it completes."
						: executionStatus === "Cancelling"
							? "Cancellation has been requested. Any available activity remains below."
							: "This run is active. The result will appear here when it completes."}
				</p>
			)}
		</div>
	);

	const resultBody =
		isComplete &&
		(execution.result != null || executionStatus === "Success") ? (
			<ExecutionResultPanel
				result={resultData?.result}
				resultType={resultData?.result_type}
				workflowName={execution.workflow_name}
				isLoading={isLoadingResult}
			/>
		) : isComplete ? (
			<p className="py-4 text-sm text-muted-foreground">
				This run did not return a result.
			</p>
		) : executionStatus === "Running" && mergedLogs.length > 0 ? null : (
			runningResult
		);

	const resultContent = (
		<div className="space-y-5">
			{resultBody}
			<ExecutionActivityFeed
				key={executionId}
				logs={mergedLogs}
				active={isActive}
				onViewLogs={() => setSelectedContentTab("logs")}
			/>
		</div>
	);

	const inputContent = (
		<PrettyInputDisplay
			inputData={(execution.input_data ?? {}) as Record<string, unknown>}
			showToggle
			defaultView="pretty"
		/>
	);

	const logsContent = (
		<ExecutionLogsPanel
			key={executionId}
			logs={mergedLogs as LogEntry[]}
			status={executionStatus}
			isConnected={isConnected}
			isLoading={isLoadingLogs}
			isPlatformAdmin={isPlatformAdmin}
			maxHeight="min(60vh, 42rem)"
			variant="primary"
		/>
	);

	const renderExtraDetails = (triggerClassName?: string) => {
		const aiUsageList = execution.ai_usage as
			| {
					provider: string;
					model: string;
					input_tokens: number;
					output_tokens: number;
					cost?: string | number | null;
			  }[]
			| undefined;
		const hasAiUsage = aiUsageList && aiUsageList.length > 0;
		const hasMetrics =
			isPlatformAdmin &&
			(execution.peak_memory_bytes != null ||
				execution.cpu_total_seconds != null);
		const hasVariables =
			isPlatformAdmin &&
			isComplete &&
			variablesData &&
			Object.keys(variablesData).length > 0;
		const hasExecutionContext = !!execution.execution_context;
		const hasExtras =
			hasAiUsage || hasMetrics || hasVariables || hasExecutionContext;

		if (!isComplete || !hasExtras) return null;

		return (
			<Collapsible>
				<CollapsibleTrigger
					className={
						triggerClassName ??
						"flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none [&[data-state=open]>svg]:rotate-180"
					}
				>
					<ChevronDown className="h-4 w-4 transition-transform duration-[var(--bf-motion-disclosure)] motion-reduce:transition-none" />
					More details
				</CollapsibleTrigger>
				<CollapsibleContent className="space-y-4 pt-3">
					<ExecutionSidebar
						executedByName={execution.executed_by_name}
						orgName={execution.org_name}
						scheduledAt={execution.scheduled_at}
						startedAt={execution.started_at}
						completedAt={execution.completed_at}
						inputData={execution.input_data}
						isComplete={isComplete}
						isPlatformAdmin={isPlatformAdmin}
						isLoading={isLoading}
						variablesData={variablesData}
						peakMemoryBytes={execution.peak_memory_bytes}
						cpuTotalSeconds={execution.cpu_total_seconds}
						durationMs={execution.duration_ms}
						aiUsage={execution.ai_usage}
						aiTotals={execution.ai_totals}
						executionContext={execution.execution_context}
						extrasOnly
					/>
				</CollapsibleContent>
			</Collapsible>
		);
	};

	// Embedded mode — single-column layout for slideout drawer
	if (embedded) {
		const actionButtons = (
			<>
				{isPlatformAdmin && isComplete && (
					<Button
						variant="outline"
						size="sm"
						onClick={() => setShowRerunDialog(true)}
						disabled={isRerunning}
						title="Rerun"
						aria-label="Rerun execution"
					>
						{isRerunning ? (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						) : (
							<RefreshCw className="h-3.5 w-3.5" />
						)}
						Rerun
					</Button>
				)}
				{(execution.status === "Running" ||
					execution.status === "Pending") && (
					<Button
						variant="ghost"
						size="icon-lg"
						onClick={() => setShowCancelDialog(true)}
						title="Cancel"
						aria-label="Cancel execution"
					>
						<XCircle className="h-3.5 w-3.5" />
					</Button>
				)}
			</>
		);

		return (
			<div className="h-full">
				{actionsContainer ? (
					createPortal(actionButtons, actionsContainer)
				) : (
					<div className="mb-3 flex justify-end gap-2">
						{actionButtons}
					</div>
				)}

				<div className="space-y-4">
					{refreshError}
					<div className="space-y-2">
						<h3 className="font-display text-lg font-semibold [overflow-wrap:anywhere]">
							{execution.workflow_name}
						</h3>
						<RunStatusBadge
							status={executionStatus as string}
							queuePosition={streamState?.queuePosition}
							waitReason={streamState?.waitReason}
						/>
					</div>
					<ExecutionActivityTrace
						status={executionStatus}
						startedAt={execution.started_at}
						completedAt={execution.completed_at}
						executedByName={execution.executed_by_name}
						orgName={execution.org_name}
						isConnected={isConnected}
						isStreamingEnabled={signalrEnabled}
						showProgressLine={false}
					/>

					{/* Error message — the triage answer; loud, copyable */}
					{execution.error_message && (
						<div
							className={`rounded-[var(--bf-radius-surface)] border p-3 ${outcomeTone}`}
						>
							<div className="flex items-start gap-2">
								<XCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
								<pre className="min-w-0 flex-1 text-sm whitespace-pre-wrap [overflow-wrap:anywhere] font-mono">
									{execution.error_message}
								</pre>
								<Button
									variant="ghost"
									size="icon"
									className="size-11 flex-shrink-0 text-inherit sm:size-7"
									onClick={() =>
										void copyWithToast(
											execution.error_message ?? "",
											isCancelled
												? "Cancellation message copied"
												: "Error copied",
										)
									}
									title={
										isCancelled
											? "Copy cancellation message"
											: "Copy error"
									}
									aria-label={
										isCancelled
											? "Copy cancellation message"
											: "Copy error"
									}
								>
									<Copy className="h-3.5 w-3.5" />
								</Button>
							</div>
						</div>
					)}

					<div
						className={
							isActive ? "execution-detail-card p-3" : "min-w-0"
						}
						data-active={isActive ? "true" : undefined}
					>
						<ExecutionInspector
							key={executionId}
							value={selectedContentTab}
							onValueChange={setSelectedContentTab}
							result={resultContent}
							input={inputContent}
							logs={logsContent}
							summary={renderExtraDetails(
								"flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none [&[data-state=open]>svg]:rotate-180",
							)}
						/>
					</div>
				</div>

				<ExecutionCancelDialog
					open={showCancelDialog}
					onOpenChange={setShowCancelDialog}
					workflowName={execution.workflow_name}
					isCancelling={isCancelling}
					error={cancelError}
					onConfirm={handleCancelExecution}
				/>

				<ExecutionRerunDialog
					open={showRerunDialog}
					onOpenChange={setShowRerunDialog}
					workflowName={execution.workflow_name}
					isRerunning={isRerunning}
					error={rerunError}
					onConfirm={handleRerunExecution}
				/>
			</div>
		);
	}

	return (
		<div className="execution-shell h-full overflow-y-auto bg-background lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden">
			{/* Page Header - hidden for embedded users (embedded prop short-circuits earlier) */}
			{!isEmbed && (
				<ExecutionPageHeader
					backLabel={usageReturn ? "Usage" : "History"}
					name={execution.workflow_name}
					status={
						<RunStatusBadge
							status={executionStatus as string}
							queuePosition={streamState?.queuePosition}
							waitReason={streamState?.waitReason}
							availableMemoryMb={streamState?.availableMemoryMb}
							requiredMemoryMb={streamState?.requiredMemoryMb}
						/>
					}
					onBack={handleBackToHistory}
					onCopyId={() =>
						void copyWithToast(
							execution.execution_id,
							"Execution ID copied",
						)
					}
					onOpenEditor={
						metadata?.workflows?.find(
							(workflow: WorkflowMetadata) =>
								workflow.name === execution.workflow_name,
						)?.source_file_path
							? handleOpenInEditor
							: undefined
					}
					onRerun={
						isPlatformAdmin && isComplete
							? () => setShowRerunDialog(true)
							: undefined
					}
					onCancel={
						execution.status === "Running" ||
						execution.status === "Pending"
							? () => setShowCancelDialog(true)
							: undefined
					}
					openingEditor={isOpeningInEditor}
					rerunning={isRerunning}
				/>
			)}

			{isEmbed && (
				<header className="border-b px-4 py-4 sm:px-6 lg:px-8">
					<RunDetailHeading
						title={execution.workflow_name}
						metadata={
							<div role="status" aria-live="polite">
								<RunStatusBadge
									status={executionStatus as string}
								/>
							</div>
						}
						actionsLabel="Execution actions"
					/>
				</header>
			)}
			<div data-page-scroll className="min-h-0 flex-1 overflow-auto">
				<div className="mx-auto flex w-full max-w-[84rem] flex-col gap-4 p-4 pb-8 sm:p-5 sm:pb-8 lg:p-6 lg:pb-10">
					{refreshError}
					<ExecutionActivityTrace
						status={executionStatus}
						startedAt={execution.started_at}
						completedAt={execution.completed_at}
						executedByName={execution.executed_by_name}
						orgName={execution.org_name}
						isConnected={isConnected}
						isStreamingEnabled={signalrEnabled}
						showProgressLine={false}
					/>
					{execution.error_message && (
						<motion.div
							initial={
								reduceMotion ? false : { opacity: 0, y: 20 }
							}
							animate={{ opacity: 1, y: 0 }}
							transition={{ duration: reduceMotion ? 0 : 0.22 }}
							data-testid="execution-error-banner"
						>
							<div
								className={`rounded-[var(--bf-radius-surface)] border p-4 ${outcomeTone}`}
							>
								<div className="flex items-start gap-3">
									<XCircle className="h-5 w-5 flex-shrink-0 mt-0.5" />
									<div className="flex-1 min-w-0">
										<p className="text-sm font-semibold">
											{isCancelled
												? "This run was cancelled"
												: "This run failed"}
										</p>
										<pre className="mt-1 text-sm whitespace-pre-wrap [overflow-wrap:anywhere] font-mono">
											{execution.error_message}
										</pre>
									</div>
									<Button
										variant="ghost"
										size="icon"
										className="size-11 flex-shrink-0 text-inherit sm:size-7"
										onClick={() =>
											void copyWithToast(
												execution.error_message ?? "",
												isCancelled
													? "Cancellation message copied"
													: "Error copied",
											)
										}
										title={
											isCancelled
												? "Copy cancellation message"
												: "Copy error"
										}
										aria-label={
											isCancelled
												? "Copy cancellation message"
												: "Copy error"
										}
									>
										<Copy className="h-4 w-4" />
									</Button>
								</div>
							</div>
						</motion.div>
					)}

					<div className="min-w-0">
						<motion.div
							initial={
								reduceMotion ? false : { opacity: 0, y: 16 }
							}
							animate={{ opacity: 1, y: 0 }}
							transition={{ duration: reduceMotion ? 0 : 0.22 }}
							className={
								isActive
									? "execution-detail-card w-full min-w-0 p-3 sm:p-4"
									: "w-full min-w-0"
							}
							data-active={isActive ? "true" : undefined}
						>
							<ExecutionInspector
								key={executionId}
								className="max-h-full"
								value={selectedContentTab}
								onValueChange={setSelectedContentTab}
								result={resultContent}
								input={inputContent}
								logs={logsContent}
								summary={renderExtraDetails()}
							/>
						</motion.div>
					</div>
				</div>
			</div>

			{/* Cancel Confirmation Dialog */}
			<ExecutionCancelDialog
				open={showCancelDialog}
				onOpenChange={setShowCancelDialog}
				workflowName={execution.workflow_name}
				isCancelling={isCancelling}
				error={cancelError}
				onConfirm={handleCancelExecution}
			/>

			{/* Rerun Confirmation Dialog */}
			<ExecutionRerunDialog
				open={showRerunDialog}
				onOpenChange={setShowRerunDialog}
				workflowName={execution.workflow_name}
				isRerunning={isRerunning}
				error={rerunError}
				onConfirm={handleRerunExecution}
			/>
		</div>
	);
}
