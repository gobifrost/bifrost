import { useParams, useNavigate, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import {
	ArrowLeft,
	XCircle,
	Loader2,
	Code2,
	RefreshCw,
	ChevronDown,
	Copy,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
	ExecutionMetadataBar,
	ExecutionStatusBadge,
	PrettyInputDisplay,
	type LogEntry,
} from "@/components/execution";
import type { components } from "@/lib/v1";
import {
	mergeLogsWithDedup,
	type ExecutionLogEntry,
} from "@/lib/executionLogs";
import { useQueryClient } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { useEffect, useState, useCallback } from "react";

type ExecutionStatus =
	| components["schemas"]["ExecutionStatus"]
	| "Cancelling"
	| "Cancelled";
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
	const { isPlatformAdmin, hasRole } = useAuth();
	const isEmbed = hasRole("EmbedUser");
	const queryClient = useQueryClient();

	// Check if we came from an execution trigger (has navigation state).
	// location.state persists across browser refreshes (React Router uses history.state),
	// so we clear it immediately after reading to prevent deferred-fetch on refresh.
	const [hasNavigationState] = useState(() => location.state != null);
	useEffect(() => {
		if (location.state != null) {
			navigate(location.pathname, { replace: true, state: null });
		}
	}, []); // eslint-disable-line react-hooks/exhaustive-deps -- clear once on mount

	// WebSocket streaming enabled state - starts enabled only for new executions from triggers
	const [signalrEnabled, setSignalrEnabled] = useState(false);

	// Fallback timer - enable fetch after 5s if WebSocket hasn't received updates
	const [fetchFallbackEnabled, setFetchFallbackEnabled] = useState(
		!hasNavigationState,
	);

	// Get streaming logs from store
	// Use stable selector to avoid infinite loops
	const streamState = useExecutionStreamStore((state) =>
		executionId ? state.streams[executionId] : undefined,
	);
	const streamingLogs = streamState?.streamingLogs ?? [];

	// Reset fallback when execution ID changes (important for rerun
	// navigation). Adjust during render with a previous-ID sentinel rather
	// than a setState-in-effect cycle.
	const [prevExecutionId, setPrevExecutionId] = useState(executionId);
	if (prevExecutionId !== executionId) {
		setPrevExecutionId(executionId);
		setFetchFallbackEnabled(!hasNavigationState);
	}

	// Fallback timer — set 5s fallback for navigation state. Timer-based
	// state transitions are a legitimate effect since they happen after a
	// scheduled callback (not synchronously in the effect body).
	useEffect(() => {
		if (hasNavigationState) {
			const timer = setTimeout(() => setFetchFallbackEnabled(true), 5000);
			return () => clearTimeout(timer);
		}
		return undefined;
	}, [executionId, hasNavigationState]);

	// Determine if we should fetch from API
	// Fetch when:
	// - Stream received update (confirms DB write), OR
	// - Fallback timer expired (5s after navigation), OR
	// - No navigation state (direct link/refresh - fetch immediately!)
	const hasReceivedUpdate = streamState?.hasReceivedUpdate ?? false;
	const shouldFetchExecution = hasReceivedUpdate || fetchFallbackEnabled;

	// State for confirmation dialogs
	const [showCancelDialog, setShowCancelDialog] = useState(false);
	const [showRerunDialog, setShowRerunDialog] = useState(false);
	const [isRerunning, setIsRerunning] = useState(false);
	const [isOpeningInEditor, setIsOpeningInEditor] = useState(false);

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
			queryKey: [
				"get",
				"/api/executions/{execution_id}",
				{ params: { path: { execution_id: executionId } } },
			],
		});
	}, [queryClient, executionId]);

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
	} = useExecution(shouldFetchExecution ? executionId : undefined, {
		// Disable polling when WebSocket is connected AND execution is not complete
		// This prevents duplicate API calls while streaming
		disablePolling: isConnected && signalrEnabled,
	});

	// Cast execution data to the correct type
	const execution = executionData as WorkflowExecution | undefined;

	// Execution status and completion check
	const executionStatus = execution?.status as ExecutionStatus | undefined;
	const isComplete =
		executionStatus === "Success" ||
		executionStatus === "Failed" ||
		executionStatus === "CompletedWithErrors" ||
		executionStatus === "Timeout" ||
		executionStatus === "Cancelled";

	// Data now comes from single API call - create adapter variables for compatibility
	const resultData = execution
		? { result: execution.result, result_type: execution.result_type }
		: undefined;
	const logsData = execution?.logs as ExecutionLogEntry[] | undefined;
	const variablesData = execution?.variables as
		| Record<string, unknown>
		| undefined;

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
			queryClient.setQueryData(
				[
					"get",
					"/api/executions/{execution_id}",
					{ params: { path: { execution_id: executionId } } },
				],
				(old: unknown) => {
					if (!old || typeof old !== "object") return old;
					return {
						...(old as Record<string, unknown>),
						status: streamStatus,
					};
				},
			);
		}
	}, [streamStatus, executionId, queryClient]);

	const handleCancelExecution = async () => {
		if (!executionId || !execution) return;

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
		} catch (error) {
			toast.error(`Failed to cancel execution: ${error}`);
			setShowCancelDialog(false);
		}
	};

	const handleRerunExecution = async () => {
		if (!execution) return;

		// Look up the workflow ID from metadata
		const workflow = metadata?.workflows?.find(
			(w: WorkflowMetadata) => w.name === execution.workflow_name,
		);

		if (!workflow?.id) {
			toast.error("Cannot rerun: workflow not found in metadata");
			setShowRerunDialog(false);
			return;
		}

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
		} catch (error) {
			toast.error(`Failed to rerun workflow: ${error}`);
			setShowRerunDialog(false);
		} finally {
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

	// Compute merged logs for the logs panel
	const mergedLogs = (() => {
		const existingLogs = (logsData as ExecutionLogEntry[]) || [];
		if (
			executionStatus === "Running" ||
			executionStatus === "Pending" ||
			executionStatus === "Cancelling"
		) {
			return mergeLogsWithDedup(existingLogs, streamingLogs);
		}
		return existingLogs;
	})();

	// Show "waiting" state when we came from trigger and haven't received data yet
	// This happens before shouldFetchExecution becomes true (waiting for WebSocket or 5s fallback)
	if (!shouldFetchExecution && !execution) {
		if (embedded) {
			return (
				<div className="flex items-center justify-center h-full p-8">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
				</div>
			);
		}
		return <PageLoader message="Waiting for execution to start..." />;
	}

	// Show loading state during initial load
	if (isLoading) {
		if (embedded) {
			return (
				<div className="flex items-center justify-center h-full p-8">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
				</div>
			);
		}
		return <PageLoader message="Loading execution details..." />;
	}

	if (error || !execution) {
		if (embedded) {
			return (
				<div className="flex flex-col items-center justify-center h-full p-8 text-center">
					<XCircle className="h-12 w-12 text-destructive" />
					<p className="text-sm text-destructive mt-4">
						{error
							? "Failed to load execution"
							: "Execution not found"}
					</p>
				</div>
			);
		}
		return (
			<div className="flex items-center justify-center min-h-[60vh] p-6">
				<motion.div
					initial={{ opacity: 0, y: 20 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: 0.3 }}
					className="max-w-md w-full space-y-6"
				>
					<div className="flex justify-center">
						<XCircle className="h-16 w-16 text-destructive" />
					</div>
					<Alert variant="destructive">
						<XCircle className="h-4 w-4" />
						<AlertTitle>Error</AlertTitle>
						<AlertDescription>
							{error
								? "Failed to load execution details. The execution may not exist or you may not have permission to view it."
								: "Execution not found"}
						</AlertDescription>
					</Alert>
					<div className="flex justify-center">
						<Button
							onClick={() => navigate("/history")}
							variant="outline"
						>
							<ArrowLeft className="mr-2 h-4 w-4" />
							Back to History
						</Button>
					</div>
				</motion.div>
			</div>
		);
	}

	// Embedded mode — single-column layout for slideout drawer
	if (embedded) {
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
			(execution.peak_memory_bytes || execution.cpu_total_seconds);
		const hasVariables =
			isPlatformAdmin &&
			isComplete &&
			variablesData &&
			Object.keys(variablesData).length > 0;
		const hasExecutionContext = !!execution.execution_context;
		const hasExtras =
			hasAiUsage || hasMetrics || hasVariables || hasExecutionContext;

		const actionButtons = (
			<>
				{isPlatformAdmin && isComplete && (
					<Button
						variant="ghost"
						size="icon"
						className="h-7 w-7"
						onClick={() => setShowRerunDialog(true)}
						disabled={isRerunning}
						title="Rerun"
					>
						{isRerunning ? (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						) : (
							<RefreshCw className="h-3.5 w-3.5" />
						)}
					</Button>
				)}
				{(execution.status === "Running" ||
					execution.status === "Pending") && (
					<Button
						variant="ghost"
						size="icon"
						className="h-7 w-7"
						onClick={() => setShowCancelDialog(true)}
						title="Cancel"
					>
						<XCircle className="h-3.5 w-3.5" />
					</Button>
				)}
			</>
		);

		return (
			<div className="h-full">
				{actionsContainer &&
					createPortal(actionButtons, actionsContainer)}

				<div className="p-4 space-y-3">
					{/* Compact metadata header */}
					<ExecutionMetadataBar
						workflowName={execution.workflow_name}
						status={executionStatus as ExecutionStatus}
						executedByName={execution.executed_by_name}
						orgName={execution.org_name}
						startedAt={execution.started_at}
						durationMs={execution.duration_ms}
						queuePosition={streamState?.queuePosition}
						waitReason={streamState?.waitReason}
						availableMemoryMb={streamState?.availableMemoryMb}
						requiredMemoryMb={streamState?.requiredMemoryMb}
					/>

					{/* Error message */}
					{execution.error_message && (
						<div className="p-3 bg-destructive/10 border border-destructive/20 rounded-lg">
							<div className="flex items-start gap-2">
								<XCircle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
								<pre className="text-sm whitespace-pre-wrap font-mono text-destructive/90 overflow-x-auto">
									{execution.error_message}
								</pre>
							</div>
						</div>
					)}

					{/* Result */}
					{isComplete && execution.result != null && (
						<ExecutionResultPanel
							result={resultData?.result}
							resultType={resultData?.result_type}
							workflowName={execution.workflow_name}
							isLoading={isLoadingResult}
						/>
					)}

					{/* Input data */}
					{execution.input_data && (
						<div className="space-y-2">
							<h4 className="text-sm font-medium text-muted-foreground">
								Input Parameters
							</h4>
							<PrettyInputDisplay
								inputData={
									execution.input_data as Record<string, unknown>
								}
								showToggle={true}
								defaultView="pretty"
							/>
						</div>
					)}

					{/* Logs */}
					<ExecutionLogsPanel
						logs={mergedLogs as LogEntry[]}
						status={executionStatus}
						isConnected={isConnected}
						isLoading={isLoadingLogs}
						isPlatformAdmin={isPlatformAdmin}
						maxHeight="50vh"
						embedded
					/>

					{/* Extra details — collapsible */}
					{isComplete && hasExtras && (
						<Collapsible>
							<CollapsibleTrigger className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors w-full py-2 [&[data-state=open]>svg]:rotate-180">
								<ChevronDown className="h-4 w-4 transition-transform duration-200" />
								More details
							</CollapsibleTrigger>
							<CollapsibleContent className="space-y-4 pt-2">
								<ExecutionSidebar
									status={
										execution.status as ExecutionStatus
									}
									workflowName={execution.workflow_name}
									executedByName={
										execution.executed_by_name
									}
									orgName={execution.org_name}
									startedAt={execution.started_at}
									completedAt={execution.completed_at}
									inputData={execution.input_data}
									isComplete={isComplete}
									isPlatformAdmin={isPlatformAdmin}
									isLoading={isLoading}
									variablesData={variablesData}
									peakMemoryBytes={
										execution.peak_memory_bytes
									}
									cpuTotalSeconds={
										execution.cpu_total_seconds
									}
									durationMs={execution.duration_ms}
									aiUsage={execution.ai_usage}
									aiTotals={execution.ai_totals}
									errorMessage={execution.error_message}
									executionContext={
										execution.execution_context
									}
									extrasOnly
								/>
							</CollapsibleContent>
						</Collapsible>
					)}
				</div>

				<ExecutionCancelDialog
					open={showCancelDialog}
					onOpenChange={setShowCancelDialog}
					workflowName={execution.workflow_name}
					onConfirm={handleCancelExecution}
				/>

				<ExecutionRerunDialog
					open={showRerunDialog}
					onOpenChange={setShowRerunDialog}
					workflowName={execution.workflow_name}
					isRerunning={isRerunning}
					onConfirm={handleRerunExecution}
				/>
			</div>
		);
	}

	return (
		<div className="h-full overflow-y-auto">
			{/* Page Header - hidden for embedded users (embedded prop short-circuits earlier) */}
			{!isEmbed && (
				<div className="sticky top-0 bg-background/80 backdrop-blur-sm border-b z-10">
					<div className="px-6 lg:px-8 py-3 space-y-1">
						{/* Row 1: Back + workflow name + status + action buttons */}
						<div className="flex items-center gap-3 min-w-0">
							<Button
								variant="ghost"
								size="icon"
								className="flex-shrink-0 h-8 w-8"
								onClick={() => navigate("/history")}
							>
								<ArrowLeft className="h-4 w-4" />
							</Button>
							<h1 className="text-lg font-semibold tracking-tight truncate">
								{execution.workflow_name}
							</h1>
							<ExecutionStatusBadge
								status={executionStatus as string}
								queuePosition={streamState?.queuePosition}
								waitReason={streamState?.waitReason}
								availableMemoryMb={streamState?.availableMemoryMb}
								requiredMemoryMb={streamState?.requiredMemoryMb}
							/>
							<div className="flex gap-1.5 flex-wrap ml-auto flex-shrink-0">
								{metadata?.workflows?.find(
									(w: WorkflowMetadata) =>
										w.name === execution.workflow_name,
								)?.source_file_path && (
									<Button
										variant="ghost"
										size="sm"
										className="h-7 text-xs"
										onClick={handleOpenInEditor}
										disabled={isOpeningInEditor}
									>
										{isOpeningInEditor ? (
											<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
										) : (
											<Code2 className="mr-1.5 h-3.5 w-3.5" />
										)}
										Editor
									</Button>
								)}
								{isPlatformAdmin && isComplete && (
									<Button
										variant="ghost"
										size="sm"
										className="h-7 text-xs"
										onClick={() => setShowRerunDialog(true)}
										disabled={isRerunning}
									>
										{isRerunning ? (
											<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
										) : (
											<RefreshCw className="mr-1.5 h-3.5 w-3.5" />
										)}
										Rerun
									</Button>
								)}
								{(execution.status === "Running" ||
									execution.status === "Pending") && (
									<Button
										variant="ghost"
										size="sm"
										className="h-7 text-xs"
										onClick={() => setShowCancelDialog(true)}
									>
										<XCircle className="mr-1.5 h-3.5 w-3.5" />
										Cancel
									</Button>
								)}
								<Button
									variant="ghost"
									size="icon"
									className="h-7 w-7"
									onClick={() => {
										navigator.clipboard.writeText(execution.execution_id);
										toast.success("Execution ID copied");
									}}
									title="Copy execution ID"
								>
									<Copy className="h-3.5 w-3.5" />
								</Button>
							</div>
						</div>
					</div>
				</div>
			)}

			{/* Two-column layout: Content on left, Sidebar on right */}
			<div className="p-6 lg:p-8">
				<div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
					{/* Left Column - Main Content (2/3 width) */}
					<div className="lg:col-span-2 space-y-6">
						{/* Result Section */}
						{isComplete && (
							<motion.div
								initial={{ opacity: 0, y: 20 }}
								animate={{ opacity: 1, y: 0 }}
								transition={{ duration: 0.3 }}
							>
								<ExecutionResultPanel
									result={resultData?.result}
									resultType={resultData?.result_type}
									workflowName={execution.workflow_name}
									isLoading={isLoadingResult}
								/>
							</motion.div>
						)}

						{/* Logs Section */}
						<motion.div
							initial={{ opacity: 0, y: 20 }}
							animate={{ opacity: 1, y: 0 }}
							transition={{ duration: 0.3, delay: 0.1 }}
						>
							<ExecutionLogsPanel
								logs={mergedLogs as LogEntry[]}
								status={executionStatus}
								isConnected={isConnected}
								isLoading={isLoadingLogs}
								isPlatformAdmin={isPlatformAdmin}
								maxHeight="70vh"
							/>
						</motion.div>
					</div>

					{/* Right Column - Sidebar (1/3 width) */}
					<ExecutionSidebar
						status={execution.status as ExecutionStatus}
						workflowName={execution.workflow_name}
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
						streamState={streamState ? {
							queuePosition: streamState.queuePosition,
							waitReason: streamState.waitReason,
							availableMemoryMb: streamState.availableMemoryMb,
							requiredMemoryMb: streamState.requiredMemoryMb,
						} : undefined}
						errorMessage={execution.error_message}
						executionContext={execution.execution_context}
					/>
				</div>
			</div>

			{/* Cancel Confirmation Dialog */}
			<ExecutionCancelDialog
				open={showCancelDialog}
				onOpenChange={setShowCancelDialog}
				workflowName={execution.workflow_name}
				onConfirm={handleCancelExecution}
			/>

			{/* Rerun Confirmation Dialog */}
			<ExecutionRerunDialog
				open={showRerunDialog}
				onOpenChange={setShowRerunDialog}
				workflowName={execution.workflow_name}
				isRerunning={isRerunning}
				onConfirm={handleRerunExecution}
			/>
		</div>
	);
}
