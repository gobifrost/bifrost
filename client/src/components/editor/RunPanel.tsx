import { Button } from "@/components/ui/button";
import { useNavigate } from "react-router-dom";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	useState,
	useEffect,
	useRef,
	useCallback,
	useMemo,
	type MutableRefObject,
} from "react";
import {
	Play,
	Loader2,
	Workflow,
	FileCode,
	AlertCircle,
	ChevronDown,
	ChevronRight,
	CheckCircle,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEditorStore } from "@/stores/editorStore";
import { useWorkflowsMetadata, useExecuteWorkflow } from "@/hooks/useWorkflows";
import { useExecutionStream } from "@/hooks/useExecutionStream";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";
import { useScopeStore } from "@/stores/scopeStore";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { getExecutionVariables } from "@/hooks/useExecutions";
import { validateWorkflow } from "@/hooks/useWorkflows";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type WorkflowExecutionResponse =
	components["schemas"]["WorkflowExecutionResponse"];
type WorkflowValidationResponse =
	components["schemas"]["WorkflowValidationResponse"];
type ValidationIssue = components["schemas"]["ValidationIssue"];

// Log entry type from execution response
interface LogEntry {
	level: string;
	message: string;
	timestamp: string;
	source: string;
}

interface RunPanelProps {
	executeRef?: MutableRefObject<(() => void) | null>;
}

/**
 * Run panel for executing workflows, data providers, and scripts
 * Shows detected file type and appropriate inputs
 */
export function RunPanel({ executeRef }: RunPanelProps) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const orgId = useScopeStore((state) => state.scope.orgId);

	const tabs = useEditorStore((state) => state.tabs);
	const activeTabIndex = useEditorStore((state) => state.activeTabIndex);
	const appendTerminalOutput = useEditorStore(
		(state) => state.appendTerminalOutput,
	);
	const setCurrentStreamingExecutionId = useEditorStore(
		(state) => state.setCurrentStreamingExecutionId,
	);

	// Compute active tab from state
	const activeTab =
		activeTabIndex >= 0 && activeTabIndex < tabs.length
			? tabs[activeTabIndex]
			: null;

	const openFile = activeTab?.file || null;
	const fileContent = activeTab?.content || "";

	const {
		data: metadata,
		isLoading: isLoadingMetadata,
		isError: metadataError,
		isFetching: metadataFetching,
		hasData: hasMetadata,
		refetch: refetchMetadata,
	} = useWorkflowsMetadata();
	const executeWorkflow = useExecuteWorkflow();
	const [isExecuting, setIsExecuting] = useState(false);
	const [isValidating, setIsValidating] = useState(false);
	const [currentExecutionId, setCurrentExecutionId] = useState<string | null>(
		null,
	);
	const [variablesExpanded, setVariablesExpanded] = useState(true);
	const [runError, setRunError] = useState<string | null>(null);
	const [retryRun, setRetryRun] = useState<(() => void) | null>(null);
	const [lastExecutionVariables, setLastExecutionVariables] = useState<
		Record<string, unknown>
	>({});

	type DetectedItem = {
		type: "workflow" | "script" | null;
		// For executables: can have multiple per file (workflows, tools, data providers)
		workflows?: WorkflowMetadata[];
		// First matching executable for convenience
		metadata?: WorkflowMetadata;
	};

	// Detect file type entirely from props/data — no state needed since the
	// result is purely a function of `openFile` and `metadata`.
	const detectedItem = useMemo<DetectedItem>(() => {
		if (!openFile || !openFile.name.endsWith(".py")) {
			return { type: null };
		}

		// Fast path: Check entity_type flag from file metadata
		// entity_type="workflow" covers both workflows and data providers (consolidated)
		if (openFile.entity_type === "workflow") {
			// If we have entity_id, look up directly
			if (openFile.entity_id && metadata?.workflows) {
				const directMatch = metadata.workflows.find(
					(w: WorkflowMetadata) => w.id === openFile.entity_id,
				);
				if (directMatch) {
					const matchingWorkflows = metadata.workflows.filter(
						(w: WorkflowMetadata) =>
							w.relative_file_path === openFile.path ||
							w.source_file_path?.endsWith(openFile.path),
					);
					return {
						type: "workflow",
						workflows: matchingWorkflows,
						metadata: directMatch,
					};
				}
			}

			// Fallback: Look up ALL matching executables for this file
			const matchingWorkflows =
				metadata?.workflows?.filter(
					(w: WorkflowMetadata) =>
						w.relative_file_path === openFile.path ||
						w.source_file_path?.endsWith(openFile.path),
				) || [];

			return {
				type: "workflow",
				workflows: matchingWorkflows,
				metadata: matchingWorkflows[0],
			};
		}

		// Slow path: File doesn't have entity_type set, check metadata (back-compat)
		if (metadata) {
			const matchingWorkflows =
				metadata.workflows?.filter(
					(w: WorkflowMetadata) =>
						w.relative_file_path === openFile.path ||
						w.source_file_path?.endsWith(openFile.path),
				) || [];

			if (matchingWorkflows.length > 0) {
				return {
					type: "workflow",
					workflows: matchingWorkflows,
					metadata: matchingWorkflows[0],
				};
			}
		}

		// Otherwise it's a regular script
		return { type: "script" };
	}, [openFile, metadata]);

	const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
		null,
	);
	const firstInputRef = useRef<HTMLInputElement>(null);
	const runContextKey = `${openFile?.path ?? ""}::${selectedWorkflowId ?? ""}`;
	const [prevRunContextKey, setPrevRunContextKey] = useState<string | null>(
		null,
	);
	if (prevRunContextKey !== runContextKey) {
		setPrevRunContextKey(runContextKey);
		setRunError(null);
		setRetryRun(null);
	}

	// Get stream state and actions from store
	const streamState = useExecutionStreamStore((state) =>
		currentExecutionId ? state.streams[currentExecutionId] : undefined,
	);

	const clearStream = useExecutionStreamStore((state) => state.clearStream);

	// Use streaming logs as the source of truth for loading state
	const isLoading =
		(streamState?.streamingLogs?.length ?? 0) > 0 || isExecuting;

	// Wrap onComplete in useCallback to prevent infinite loop
	// Without this, onComplete is recreated on every render, triggering useExecutionStream effect
	const handleStreamComplete = useCallback(async (executionId: string) => {
		// Fetch variables when execution completes
		try {
			const variablesData = await getExecutionVariables(executionId);
			setLastExecutionVariables(
				(variablesData || {}) as Record<string, unknown>,
			);
		} catch (error) {
			console.error("Failed to fetch variables:", error);
		}
	}, []); // Empty deps - this function is stable

	// Stream real-time logs for the current execution
	useExecutionStream({
		executionId: currentExecutionId || "",
		enabled: !!currentExecutionId,
		onComplete: handleStreamComplete,
	});

	// When execution completes, move streaming logs to terminal output. The
	// cleanup is deferred to a microtask so the synchronous body of the
	// effect does not directly invoke setState (set-state-in-effect rule).
	useEffect(() => {
		if (!streamState?.isComplete || !currentExecutionId) return;
		queueMicrotask(() => {
			// Helper to get completion message and level based on status
			const getCompletionMessage = (
				status: string,
				executionId: string,
			) => {
				const link = `[View Details](/history/${executionId})`;
				switch (status) {
					case "Success":
						return {
							message: `✓ Execution completed successfully: ${link}`,
							level: "SUCCESS",
						};
					case "Failed":
						return {
							message: `✗ Execution failed: ${link}`,
							level: "ERROR",
						};
					case "CompletedWithErrors":
						return {
							message: `⚠ Execution completed with errors: ${link}`,
							level: "WARNING",
						};
					case "Timeout":
						return {
							message: `✗ Execution timed out: ${link}`,
							level: "ERROR",
						};
					case "Cancelled":
						return {
							message: `✗ Execution cancelled: ${link}`,
							level: "WARNING",
						};
					default:
						return {
							message: `Execution completed with status: ${status} - ${link}`,
							level: "INFO",
						};
				}
			};

			const completion = getCompletionMessage(
				streamState.status || "Unknown",
				currentExecutionId,
			);

			// Append logs + completion message to terminal output
			appendTerminalOutput({
				executionId: currentExecutionId,
				loggerOutput: [
					...(streamState.streamingLogs || []).map((log) => ({
						level: log.level,
						message: log.message,
						timestamp: log.timestamp,
						source: "stream",
					})),
					{
						level: completion.level,
						message: completion.message,
						timestamp: new Date().toISOString(),
						source: "system",
					},
				],
				variables: {},
				status: streamState.status || "Unknown",
				error: undefined,
			});

			// Show completion toast based on status
			if (streamState.status === "Success") {
				toast.success("Workflow completed successfully");
			} else if (streamState.status === "Failed") {
				toast.error("Workflow execution failed");
			}

			// Clear execution state
			const executionId = currentExecutionId;
			setCurrentExecutionId(null);
			setCurrentStreamingExecutionId(null);
			setIsExecuting(false);

			// Clean up stream from store
			if (executionId) {
				clearStream(executionId);
			}
		});
		// Only depend on isComplete and currentExecutionId - we don't want to re-run when logs change
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [streamState?.isComplete, currentExecutionId]);

	// Create a stable dependency for workflow changes based on IDs, not array reference
	// This prevents infinite loops from array reference instability
	const workflowIds = useMemo(() => {
		return (detectedItem.workflows || []).map((w) => w.id).join(",");
	}, [detectedItem.workflows]);

	// Auto-select workflow when there's only one, reset when file changes.
	// Adjust during render with a previous-IDs sentinel rather than via
	// setState-in-effect.
	const [prevWorkflowIds, setPrevWorkflowIds] = useState<string | null>(null);
	if (prevWorkflowIds !== workflowIds) {
		setPrevWorkflowIds(workflowIds);
		const workflows = detectedItem.workflows || [];
		if (workflows.length === 1) {
			// Single workflow: auto-select
			setSelectedWorkflowId(workflows[0].id);
		} else if (workflows.length === 0) {
			// No workflows: clear selection
			setSelectedWorkflowId(null);
		}
		// For multiple workflows: don't auto-select, let user choose
	}

	// Get the currently selected workflow metadata
	const selectedWorkflow = useMemo(() => {
		if (!selectedWorkflowId || !detectedItem.workflows) return undefined;
		return detectedItem.workflows.find((w) => w.id === selectedWorkflowId);
	}, [selectedWorkflowId, detectedItem.workflows]);

	const handleExecuteScript = useCallback(async () => {
		if (!openFile || !fileContent) {
			toast.error("No file content to execute");
			return;
		}

		// Get filename without extension to use as identifier
		const fileName = openFile.name.replace(".py", "");

		setIsExecuting(true);
		setRunError(null);
		setRetryRun(() => () => void handleExecuteScript());
		setLastExecutionVariables({}); // Clear variables on new execution
		try {
			// Encode file content as base64
			const codeBase64 = btoa(fileContent);

			// Execute script via workflow API with transient flag and code
			const result = (await executeWorkflow.mutateAsync({
				body: {
					workflow_id: null,
					input_data: {},
					form_id: null,
					transient: true, // Editor executions are transient (no DB writes)
					code: codeBase64, // Base64-encoded script content
					script_name: fileName, // Script identifier for logging
				},
			})) as WorkflowExecutionResponse;

			// For synchronous executions, logs come back immediately in the response
			// Display them directly instead of waiting for streaming
			if (result.logs && result.logs.length > 0) {
				appendTerminalOutput({
					loggerOutput: result.logs.map(
						(log: Record<string, unknown>) => {
							const logEntry: LogEntry = {
								level: String(log["level"] || "INFO"),
								message: String(log["message"] || ""),
								timestamp: log["timestamp"]
									? String(log["timestamp"])
									: new Date().toISOString(),
								source: String(log["source"] || "script"),
							};
							return logEntry;
						},
					),
					variables: {},
					status: result.status || "Unknown",
					error: result.error || undefined,
				});
				// Set variables directly for synchronous execution
				setLastExecutionVariables(
					(result.variables as Record<string, unknown>) || {},
				);
				// Synchronous execution - done immediately
				setIsExecuting(false);
				setRetryRun(null);
				setRunError(null);

				// Show completion toast for sync execution
				if (result.status === "Success") {
					toast.success("Script executed successfully", {
						description: result.duration_ms
							? `Completed in ${result.duration_ms}ms`
							: undefined,
					});
				} else {
					toast.error("Script execution failed", {
						description: result.error || "Unknown error",
					});
				}
			} else {
				// No immediate logs - this is an async execution
				// Enable streaming for this execution
				// Keep isExecuting true - it will be cleared when streaming completes

				// Initialize the stream in the store and add "started" message
				const store = useExecutionStreamStore.getState();
				store.startStreaming(result.execution_id, "Running");
				store.appendLog(result.execution_id, {
					level: "INFO",
					message: `Script execution started (ID: ${result.execution_id})`,
					timestamp: new Date().toISOString(),
				});

				// Set execution ID to trigger useExecutionStream hook
				setCurrentExecutionId(result.execution_id);
				setCurrentStreamingExecutionId(result.execution_id);
			}
		} catch (error) {
			// On error, clear executing state and show in terminal
			setIsExecuting(false);

			// Push error to terminal (no toast - terminal is the primary feedback)
			const errorMessage = getErrorMessage(
				error,
				"Unknown error occurred",
			);
			setRunError(`Could not execute script. ${errorMessage}`);
			appendTerminalOutput({
				loggerOutput: [
					{
						level: "ERROR",
						message: `Failed to execute script: ${errorMessage}`,
						source: "system",
						timestamp: new Date().toISOString(),
					},
				],
				variables: {},
				status: "Failed",
				error: undefined,
			});
		}
	}, [
		openFile,
		fileContent,
		executeWorkflow,
		appendTerminalOutput,
		setCurrentStreamingExecutionId,
	]);

	const handleExecuteWorkflow = async (params: Record<string, unknown>) => {
		// Use selectedWorkflow (derived from selectedWorkflowId) for execution
		if (detectedItem.type !== "workflow" || !selectedWorkflow) return;

		performance.mark("workflow-execute-start");

		setIsExecuting(true);
		setRunError(null);
		setRetryRun(() => () => void handleExecuteWorkflow(params));
		setLastExecutionVariables({}); // Clear variables on new execution
		try {
			const result = (await executeWorkflow.mutateAsync({
				body: {
					workflow_id: selectedWorkflow.id,
					input_data: params,
					form_id: null,
					transient: true, // Editor executions are transient (no DB writes)
					code: null,
					script_name: null,
				},
			})) as WorkflowExecutionResponse;

			performance.mark("workflow-execute-end");
			performance.measure(
				"workflow-execute",
				"workflow-execute-start",
				"workflow-execute-end",
			);

			// For synchronous executions, logs come back immediately in the response
			// Display them directly instead of waiting for streaming
			if (result.logs && result.logs.length > 0) {
				appendTerminalOutput({
					loggerOutput: result.logs.map(
						(log: Record<string, unknown>) => {
							const logEntry: LogEntry = {
								level: String(log["level"] || "INFO"),
								message: String(log["message"] || ""),
								timestamp: log["timestamp"]
									? String(log["timestamp"])
									: new Date().toISOString(),
								source: String(log["source"] || "workflow"),
							};
							return logEntry;
						},
					),
					variables: {},
					status: result.status || "Unknown",
					error: result.error || undefined,
				});
				// Set variables directly for synchronous execution
				setLastExecutionVariables(
					(result.variables as Record<string, unknown>) || {},
				);
				// Synchronous execution - done immediately
				setIsExecuting(false);
				setRetryRun(null);
				setRunError(null);

				// Show completion toast for sync execution
				if (result.status === "Success") {
					toast.success("Workflow executed successfully");
				} else if (result.status === "Failed") {
					toast.error("Workflow execution failed", {
						description: result.error || undefined,
					});
				}
			} else {
				// No immediate logs - this is an async execution
				// Enable streaming for this execution
				// Keep isExecuting true - it will be cleared when streaming completes

				// Initialize the stream in the store and add "started" message
				const store = useExecutionStreamStore.getState();
				store.startStreaming(result.execution_id, "Running");
				store.appendLog(result.execution_id, {
					level: "INFO",
					message: `Workflow execution started: [View Details](/history/${result.execution_id})`,
					timestamp: new Date().toISOString(),
				});

				// Set execution ID to trigger useExecutionStream hook
				setCurrentExecutionId(result.execution_id);
				setCurrentStreamingExecutionId(result.execution_id);
			}
		} catch (error) {
			// On error, clear executing state and show in terminal
			setIsExecuting(false);

			// Push error to terminal (no toast - terminal is the primary feedback)
			const errorMessage = getErrorMessage(
				error,
				"Unknown error occurred",
			);
			setRunError(`Could not execute workflow. ${errorMessage}`);
			appendTerminalOutput({
				loggerOutput: [
					{
						level: "ERROR",
						message: `Failed to execute workflow: ${errorMessage}`,
						source: "system",
						timestamp: new Date().toISOString(),
					},
				],
				variables: {},
				status: "Failed",
				error: undefined,
			});
		}
	};

	const handleValidateWorkflow = useCallback(async () => {
		if (!openFile || !fileContent) {
			toast.error("No file to validate");
			return;
		}

		setIsValidating(true);
		try {
			// Call validation API
			const result = (await validateWorkflow(
				openFile.path,
				fileContent,
			)) as WorkflowValidationResponse;

			// Build terminal output based on validation results
			const logs: LogEntry[] = [];

			if (result.valid) {
				// Success - workflow is valid
				logs.push({
					level: "SUCCESS",
					message: `✓ Workflow validation passed: ${result.metadata?.name || openFile.name}`,
					source: "validation",
					timestamp: new Date().toISOString(),
				});

				if (result.metadata) {
					logs.push({
						level: "INFO",
						message: `  Description: ${result.metadata.description}`,
						source: "validation",
						timestamp: new Date().toISOString(),
					});

					if (
						result.metadata.parameters &&
						result.metadata.parameters.length > 0
					) {
						logs.push({
							level: "INFO",
							message: `  Parameters: ${result.metadata.parameters.length}`,
							source: "validation",
							timestamp: new Date().toISOString(),
						});
					}

					if (result.metadata.category) {
						logs.push({
							level: "INFO",
							message: `  Category: ${result.metadata.category}`,
							source: "validation",
							timestamp: new Date().toISOString(),
						});
					}
				}

				// Show warnings if any
				const warnings = (result.issues || []).filter(
					(i: ValidationIssue) => i.severity === "warning",
				);
				if (warnings.length > 0) {
					logs.push({
						level: "WARNING",
						message: `⚠ ${warnings.length} warning(s):`,
						source: "validation",
						timestamp: new Date().toISOString(),
					});
					warnings.forEach((warning: ValidationIssue) => {
						logs.push({
							level: "WARNING",
							message: `  ${warning.message}`,
							source: "validation",
							timestamp: new Date().toISOString(),
						});
					});
				}

				toast.success("Workflow is valid!");

				// Invalidate workflows metadata to trigger refetch and update the Run panel
				await queryClient.invalidateQueries({
					queryKey: ["workflows", "metadata", orgId],
				});
			} else {
				// Failed validation - show errors
				logs.push({
					level: "ERROR",
					message: `✗ Workflow validation failed for ${openFile.name}`,
					source: "validation",
					timestamp: new Date().toISOString(),
				});

				const errors = (result.issues || []).filter(
					(i: ValidationIssue) => i.severity === "error",
				);
				errors.forEach((error: ValidationIssue) => {
					const lineInfo = error.line ? `[Line ${error.line}] ` : "";
					logs.push({
						level: "ERROR",
						message: `  ${lineInfo}${error.message}`,
						source: "validation",
						timestamp: new Date().toISOString(),
					});
				});

				toast.error("Workflow validation failed", {
					description: `${errors.length} error(s) found`,
				});
			}

			// Append validation results to terminal
			appendTerminalOutput({
				loggerOutput: logs,
				variables: {},
				status: result.valid ? "Success" : "Failed",
				executionId: `validation-${Date.now()}`,
				error: result.valid ? undefined : "Validation failed",
			});
		} catch (error) {
			setIsValidating(false);
			toast.error("Failed to validate workflow", {
				description:
					error instanceof Error ? error.message : String(error),
			});
		} finally {
			setIsValidating(false);
		}
	}, [openFile, fileContent, appendTerminalOutput, queryClient, orgId]);

	// Register execute callback via ref so EditorLayout can call directly
	const handleExecuteEvent = useCallback(() => {
		if (isLoadingMetadata || (metadataError && !hasMetadata)) return;
		// Check if there are parameters (use selectedWorkflow for workflows)
		const hasParameters =
			detectedItem.type === "workflow" &&
			selectedWorkflow &&
			selectedWorkflow.parameters &&
			(selectedWorkflow.parameters.length ?? 0) > 0;

		if (hasParameters && firstInputRef.current) {
			// Focus first input if there are parameters
			setTimeout(() => firstInputRef.current?.focus(), 50);
		} else if (detectedItem.type === "script") {
			// Execute script immediately if no parameters
			handleExecuteScript();
		}
	}, [
		detectedItem,
		selectedWorkflow,
		handleExecuteScript,
		isLoadingMetadata,
		metadataError,
		hasMetadata,
	]);

	useEffect(() => {
		if (executeRef) {
			executeRef.current = handleExecuteEvent;
		}
		return () => {
			if (executeRef) {
				executeRef.current = null;
			}
		};
	}, [handleExecuteEvent, executeRef]);

	// No file open
	if (!openFile) {
		return (
			<div className="flex h-full flex-col items-center justify-center text-center">
				<FileCode className="h-12 w-12 mb-3 text-muted-foreground" />
				<p className="text-sm text-muted-foreground">
					Open a Python file to run it
				</p>
			</div>
		);
	}

	// Not a Python file
	if (!openFile.name.endsWith(".py")) {
		return (
			<div className="flex h-full flex-col items-center justify-center text-center">
				<AlertCircle className="h-12 w-12 mb-3 text-muted-foreground" />
				<p className="text-sm text-muted-foreground">
					Only Python files can be executed
				</p>
			</div>
		);
	}

	// Loading metadata
	if (isLoadingMetadata) {
		return (
			<div className="flex h-full flex-col items-center justify-center">
				<Loader2 className="h-8 w-8 motion-safe:animate-spin text-muted-foreground" />
				<p className="text-sm text-muted-foreground mt-3">
					Detecting file type...
				</p>
			</div>
		);
	}

	const metadataFeedback = metadataError ? (
		<Alert className="m-3 w-auto min-w-0 shrink-0">
			<AlertTitle>Could not load workflows</AlertTitle>
			<AlertDescription className="min-w-0 space-y-3">
				<p className="[overflow-wrap:anywhere]">
					{hasMetadata
						? "Previously loaded workflows are still available."
						: "Retry to load the run controls for this file. Your code has been kept."}
				</p>
				<Button
					type="button"
					variant="outline"
					className="h-auto min-h-11 max-w-full whitespace-normal"
					disabled={metadataFetching}
					onClick={() => void refetchMetadata()}
				>
					{metadataFetching ? "Retrying…" : "Retry workflows"}
				</Button>
			</AlertDescription>
		</Alert>
	) : null;
	if (metadataError && !hasMetadata) return metadataFeedback;
	if (detectedItem.type === "workflow" && !detectedItem.workflows?.length)
		return (
			<div className="min-w-0 space-y-3 p-3">
				{metadataFeedback}
				<p className="text-sm leading-6 text-muted-foreground">
					No workflow metadata was found for this file. Save your
					workflow, then refresh to load its run controls.
				</p>
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={metadataFetching}
					onClick={() => void refetchMetadata()}
				>
					Refresh workflows
				</Button>
			</div>
		);
	// Render based on detected type
	return (
		<div className="flex h-full flex-col">
			{metadataFeedback}
			{runError ? (
				<Alert
					variant="destructive"
					className="mx-3 mt-3 w-[calc(100%-1.5rem)] rounded-[var(--bf-radius-surface)]"
				>
					<AlertTitle>Run failed</AlertTitle>
					<AlertDescription className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<span>{runError}</span>
						{retryRun ? (
							<Button
								type="button"
								variant="outline"
								className="min-h-11 shrink-0"
								onClick={retryRun}
							>
								Retry last submission
							</Button>
						) : null}
					</AlertDescription>
				</Alert>
			) : null}
			{/* Content */}
			<div className="flex-1 overflow-auto">
				{detectedItem.type === "workflow" && (
					<>
						<div className="border-b px-3 py-2">
							<div className="flex items-center gap-2">
								<Workflow className="h-4 w-4 text-primary" />
								<div className="flex-1">
									<h3 className="text-sm font-semibold">
										Workflow
									</h3>
									<p className="text-xs text-muted-foreground">
										{openFile.name}
									</p>
								</div>
							</div>

							{/* Workflow selector: single workflow shows display card, multiple shows dropdown */}
							{(detectedItem.workflows?.length ?? 0) > 1 ? (
								// Multiple workflows: show Select dropdown
								<div className="mt-2">
									<Select
										value={selectedWorkflowId || ""}
										onValueChange={setSelectedWorkflowId}
									>
										<SelectTrigger
											aria-label="Select workflow"
											className="w-full"
										>
											<SelectValue placeholder="Select a workflow to run" />
										</SelectTrigger>
									<SelectContent className="z-[200]">
										{detectedItem.workflows
											?.filter((w) => w.type !== "service")
											.map((w) => (
												<SelectItem
													key={w.id}
													value={w.id}
												>
													<div className="flex flex-col items-start">
														<span>
															{w.name}
														</span>
														{w.description && (
															<span className="text-xs text-muted-foreground">
																{
																	w.description
																}
															</span>
														)}
													</div>
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								</div>
							) : selectedWorkflow?.description ? (
								// Single workflow with description
								<p className="text-xs text-muted-foreground mt-2">
									{selectedWorkflow.description}
								</p>
							) : null}
						</div>

						{/* Parameters form - only show when a runnable workflow is selected */}
						{selectedWorkflow?.type === "service" ? (
							<div className="p-3">
								<p className="text-sm leading-6 text-muted-foreground">
									{selectedWorkflow.name} is a supervised
									service — it runs under start/stop control,
									not one-shot execution. Manage it from the
									Services tab.
								</p>
								<Button
									type="button"
									variant="outline"
									className="mt-2 min-h-11"
									onClick={() => navigate("/workflows")}
								>
									Open Services
								</Button>
							</div>
						) : selectedWorkflow ? (
							<div className="p-3">
								<WorkflowParametersForm
									key={selectedWorkflowId} // Reset form when workflow changes
									parameters={
										selectedWorkflow.parameters || []
									}
									onExecute={handleExecuteWorkflow}
									isExecuting={isLoading}
									executeButtonText="Run Workflow"
								/>
							</div>
						) : (detectedItem.workflows?.length ?? 0) > 1 ? (
							// Multiple workflows but none selected
							<div className="p-3 text-center text-muted-foreground text-sm">
								Select a workflow to see its parameters
							</div>
						) : null}

						{/* Variables Section - show from last execution */}
						{Object.keys(lastExecutionVariables).length > 0 && (
							<div className="border-t">
								<button
									onClick={() =>
										setVariablesExpanded(!variablesExpanded)
									}
									className="w-full flex items-center justify-between px-4 py-2 hover:bg-muted/50 transition-colors"
								>
									<span className="text-sm font-medium">
										VARIABLES
									</span>
									{variablesExpanded ? (
										<ChevronDown className="h-4 w-4 text-muted-foreground" />
									) : (
										<ChevronRight className="h-4 w-4 text-muted-foreground" />
									)}
								</button>
								{variablesExpanded && (
									<div className="py-2 px-3 overflow-x-auto">
										<VariablesTreeView
											data={lastExecutionVariables}
										/>
									</div>
								)}
							</div>
						)}
					</>
				)}

				{detectedItem.type === "script" && (
					<>
						<div className="m-3 mb-0">
							<div className="rounded-md ring-1 ring-foreground/5 flex items-center gap-3 p-2 bg-muted/50">
								<Button
									type="button"
									variant="default"
									size="icon"
									aria-label="Run script"
									onClick={handleExecuteScript}
									disabled={isLoading}
									className="size-11 shrink-0 sm:size-8"
								>
									{isLoading ? (
										<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									) : (
										<Play className="h-4 w-4" />
									)}
								</Button>
								<Button
									type="button"
									variant="outline"
									size="icon"
									aria-label="Validate workflow"
									onClick={handleValidateWorkflow}
									disabled={isValidating}
									className="size-11 shrink-0 sm:size-8"
									title="Validate Workflow"
								>
									{isValidating ? (
										<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									) : (
										<CheckCircle className="h-4 w-4" />
									)}
								</Button>
								<div className="flex items-center gap-2 flex-1 min-w-0">
									<FileCode className="h-4 w-4 text-muted-foreground flex-shrink-0" />
									<div className="min-w-0">
										<h3 className="text-sm font-semibold">
											Script
										</h3>
										<p className="text-xs text-muted-foreground truncate">
											{openFile.name}
										</p>
									</div>
								</div>
							</div>
						</div>

						{/* Variables Section - show from last execution */}
						{Object.keys(lastExecutionVariables).length > 0 && (
							<div className="border-t">
								<button
									onClick={() =>
										setVariablesExpanded(!variablesExpanded)
									}
									className="w-full flex items-center justify-between px-4 py-2 hover:bg-muted/50 transition-colors"
								>
									<span className="text-sm font-medium">
										VARIABLES
									</span>
									{variablesExpanded ? (
										<ChevronDown className="h-4 w-4 text-muted-foreground" />
									) : (
										<ChevronRight className="h-4 w-4 text-muted-foreground" />
									)}
								</button>
								{variablesExpanded && (
									<div className="py-2 px-3 overflow-x-auto">
										<VariablesTreeView
											data={lastExecutionVariables}
										/>
									</div>
								)}
							</div>
						)}
					</>
				)}
			</div>
		</div>
	);
}
