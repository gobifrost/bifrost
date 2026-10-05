/**
 * ToolExecutionCard Component
 *
 * Displays tool/workflow execution status in chat with:
 * - Status badge (pending, running, success, failed)
 * - Live log streaming (during execution)
 * - Input parameters (info popover)
 * - Result display using PrettyInputDisplay
 *
 * Architecture:
 * - Accepts executionId as primary prop
 * - Fetches all data (result, status, logs) from executions API
 * - Supports streaming state override for live updates during execution
 */

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
	Circle,
	CheckCircle2,
	XCircle,
	Clock,
	Info,
	ChevronDown,
	ChevronRight,
	Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useExecutionStream } from "@/hooks/useExecutionStream";
import { useExecutionStreamStore } from "@/stores/executionStreamStore";
import {
	isNotFoundError,
	useExecution,
	useExecutionLogs,
} from "@/hooks/useExecutions";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { PrettyInputDisplay } from "@/components/execution/PrettyInputDisplay";
import { ToolOutputDisplay } from "@/components/chat/ToolOutputDisplay";
import { RunRemovedNotice } from "@/components/execution/RunRemovedNotice";
import type { components } from "@/lib/v1";
import { useReducedMotion } from "framer-motion";

type ToolCall = components["schemas"]["ToolCall"];
type ExecutionStatus = components["schemas"]["ExecutionStatus"];
type MessagePublic = components["schemas"]["MessagePublic"];

// Stable empty array to prevent re-render loops in Zustand selectors
const EMPTY_LOGS: { level: string; message: string; timestamp?: string }[] = [];

export type ToolExecutionStatus =
	| "pending"
	| "running"
	| "success"
	| "failed"
	| "timeout";

export interface ToolExecutionLog {
	level: "debug" | "info" | "warning" | "error";
	message: string;
	timestamp?: string;
}

/** Streaming state passed from chat for live updates during execution */
export interface StreamingToolState {
	status: ToolExecutionStatus;
	logs: ToolExecutionLog[];
	result?: unknown;
	error?: string;
	durationMs?: number;
}

/** Legacy interface for backward compatibility */
export interface ToolExecutionState {
	toolCall: ToolCall;
	status: ToolExecutionStatus;
	executionId?: string;
	logs: ToolExecutionLog[];
	result?: unknown;
	resultType?: "json" | "html" | "text";
	error?: string;
	durationMs?: number;
	startedAt?: string;
}

/** Map API ExecutionStatus to card ToolExecutionStatus */
function mapExecutionStatus(
	apiStatus: ExecutionStatus | undefined,
): ToolExecutionStatus {
	switch (apiStatus) {
		case "Pending":
			return "pending";
		case "Running":
		case "Cancelling":
			return "running";
		case "Success":
		case "CompletedWithErrors":
			return "success";
		case "Failed":
		case "Cancelled":
			return "failed";
		case "Timeout":
			return "timeout";
		default:
			return "pending";
	}
}

interface ToolExecutionCardProps {
	/** Execution ID for fetching data from API (primary mode) */
	executionId?: string;
	/** Tool call info for display (name, arguments) */
	toolCall: ToolCall;
	/** Override with streaming state during live execution */
	streamingState?: StreamingToolState;
	/** Whether this execution is currently streaming */
	isStreaming?: boolean;
	/** Legacy: Full execution state (deprecated, use executionId instead) */
	execution?: ToolExecutionState;
	/** Whether a tool result message exists (indicates completion for non-execution tools) */
	hasResultMessage?: boolean;
	/** The tool call message, whose recorded outcome stands in for a removed execution */
	toolCallMessage?: Pick<MessagePublic, "tool_state" | "tool_result">;
	className?: string;
}

const statusConfig: Record<
	ToolExecutionStatus,
	{
		icon: typeof Circle;
		label: string;
		className: string;
		badgeVariant: "default" | "secondary" | "destructive" | "outline";
	}
> = {
	pending: {
		icon: Clock,
		label: "Pending",
		className: "text-muted-foreground",
		badgeVariant: "secondary",
	},
	running: {
		icon: Loader2,
		label: "Running",
		className: "text-[var(--bf-info)]",
		badgeVariant: "default",
	},
	success: {
		icon: CheckCircle2,
		label: "Success",
		className: "text-[var(--bf-success)]",
		badgeVariant: "outline",
	},
	failed: {
		icon: XCircle,
		label: "Failed",
		className: "text-destructive",
		badgeVariant: "destructive",
	},
	timeout: {
		icon: Clock,
		label: "Timeout",
		className: "text-[var(--bf-warning)]",
		badgeVariant: "outline",
	},
};

/** Map a tool call message's recorded state to a finished card status */
function recordedToolStatus(
	toolState: MessagePublic["tool_state"],
): ToolExecutionStatus | undefined {
	if (toolState === "completed") return "success";
	if (toolState === "error") return "failed";
	return undefined;
}

/**
 * Check if result is a CallToolResult structure and extract content.
 */
function extractMcpContent(
	result: unknown
): { text: string; structured: unknown } | null {
	if (!result || typeof result !== "object") {
		return null;
	}

	const mcpResult = result as {
		content?: Array<{ type: string; text?: string }>;
		structuredContent?: unknown;
	};

	// Check for CallToolResult structure
	if (!mcpResult.content || !Array.isArray(mcpResult.content)) {
		return null;
	}

	// Extract text from TextContent blocks
	const textContent = mcpResult.content
		.filter((c) => c.type === "text" && typeof c.text === "string")
		.map((c) => c.text)
		.join("\n");

	if (!textContent) {
		return null;
	}

	return {
		text: textContent,
		structured: mcpResult.structuredContent,
	};
}

export function ToolExecutionCard({
	executionId,
	toolCall,
	streamingState,
	isStreaming = false,
	execution,
	hasResultMessage = false,
	toolCallMessage,
	className,
}: ToolExecutionCardProps) {
	// Auto-expand results when execution completes
	const [isResultOpen, setIsResultOpen] = useState(false);
	const logsEndRef = useRef<HTMLDivElement>(null);
	const prefersReducedMotion = useReducedMotion();

	// Resolve executionId from props or legacy execution object
	const resolvedExecutionId = executionId ?? execution?.executionId;
	const resolvedToolCall = toolCall ?? execution?.toolCall;

	// Fetch execution data from API (disabled during streaming)
	const {
		data: apiExecution,
		isLoading: isLoadingExecution,
		error: executionError,
	} = useExecution(
		resolvedExecutionId,
		{ disablePolling: isStreaming }, // Disable polling during streaming
	);

	// Run retention removed the execution: the tool call message's recorded
	// outcome is all that remains.
	const executionRemoved = isNotFoundError(executionError);
	const removedToolCall = executionRemoved ? toolCallMessage : undefined;
	const recordedStatus = recordedToolStatus(removedToolCall?.tool_state);

	// Determine status: streaming state > API data > legacy execution >
	// recorded tool call outcome > has result > pending
	const status: ToolExecutionStatus =
		isStreaming && streamingState
			? streamingState.status
			: apiExecution
				? mapExecutionStatus(apiExecution.status)
				: execution?.status
					? execution.status
					: (recordedStatus ??
						(hasResultMessage ? "success" : "pending"));

	// Determine completion status (needed for log fetching)
	const isComplete =
		status === "success" || status === "failed" || status === "timeout";

	// Subscribe to execution logs via WebSocket when running
	useExecutionStream({
		executionId: resolvedExecutionId || "",
		enabled: !!resolvedExecutionId && status === "running",
	});

	// Get streaming logs from the execution stream store
	const streamingLogs = useExecutionStreamStore((state) =>
		resolvedExecutionId
			? (state.streams[resolvedExecutionId]?.streamingLogs ?? EMPTY_LOGS)
			: EMPTY_LOGS,
	);

	// Fetch persisted logs when result section is expanded and execution is complete
	const { data: persistedLogs } = useExecutionLogs(
		resolvedExecutionId,
		isComplete && isResultOpen && !!resolvedExecutionId,
	);

	// Determine logs to display: streaming logs > persisted logs > streaming state logs > legacy logs
	const baseLogs = streamingState?.logs ?? execution?.logs ?? [];
	const displayLogs =
		status === "running" && streamingLogs.length > 0
			? streamingLogs
			: persistedLogs && persistedLogs.length > 0
				? persistedLogs
				: baseLogs;

	// Determine result: streaming state > API data > legacy execution >
	// recorded tool call outcome
	const result =
		isStreaming && streamingState?.result !== undefined
			? streamingState.result
			: (apiExecution?.result ??
				execution?.result ??
				(recordedStatus === "success"
					? removedToolCall?.tool_result
					: undefined));

	// Determine error: streaming state > API data > legacy execution >
	// recorded tool call outcome
	const error =
		isStreaming && streamingState?.error
			? streamingState.error
			: (apiExecution?.error_message ??
				execution?.error ??
				(recordedStatus === "failed"
					? (removedToolCall?.tool_result as { error?: string } | null)
							?.error
					: undefined));

	// Determine duration: streaming state > API data > legacy execution
	const durationMs =
		isStreaming && streamingState?.durationMs !== undefined
			? streamingState.durationMs
			: (apiExecution?.duration_ms ?? execution?.durationMs);

	const config = statusConfig[status];
	const StatusIcon = config.icon;
	const hasResult = result !== undefined && result !== null;
	const latestLog =
		displayLogs.length > 0 ? displayLogs[displayLogs.length - 1] : null;

	// Auto-scroll logs when new ones arrive
	useEffect(() => {
		if (status === "running" && logsEndRef.current) {
			logsEndRef.current.scrollTo({
				top: logsEndRef.current.scrollHeight,
				behavior: prefersReducedMotion ? "auto" : "smooth",
			});
		}
	}, [displayLogs.length, prefersReducedMotion, status]);

	// Format duration
	const formatDuration = (ms: number) => {
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	};

	// Loading state when fetching execution data and no streaming/legacy data available
	if (isLoadingExecution && !isStreaming && !execution) {
		return (
			<div
				className={cn(
					"overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card shadow-sm",
					className,
				)}
			>
				<div className="flex items-center gap-2 px-3 py-2 bg-muted/30">
					<Skeleton className="h-5 w-16" />
					<Skeleton className="h-4 w-24" />
				</div>
			</div>
		);
	}

	// Guard: need a tool call to render
	if (!resolvedToolCall) {
		return null;
	}

	return (
		<div
			className={cn(
				"overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card shadow-sm",
				status === "running" && "border-[var(--bf-info)]/40",
				status === "failed" && "border-[var(--bf-danger)]/40",
				className,
			)}
		>
			{/* Header */}
			<div className="flex items-start justify-between gap-3 px-3 py-3 sm:items-center sm:px-4">
				<div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:flex-wrap sm:items-center">
					{/* Status Badge */}
					<Badge
						variant={config.badgeVariant}
						className={cn(
							"gap-1.5 rounded-[var(--bf-radius-control)] px-2.5 py-1 font-normal leading-snug",
							status === "running" && "motion-safe:animate-pulse",
						)}
					>
						<StatusIcon
							className={cn(
								"h-3.5 w-3.5",
								config.className,
								status === "running" && "motion-safe:animate-spin",
							)}
						/>
						<span className="whitespace-normal">{config.label}</span>
					</Badge>

					{/* Tool Name */}
					<span className="min-w-0 flex-1 text-sm font-medium leading-snug [overflow-wrap:anywhere]">
						{resolvedToolCall.name}
					</span>
				</div>

				<div className="flex shrink-0 items-center gap-2">
					{/* Duration */}
					{durationMs !== undefined && (
						<span className="text-xs text-muted-foreground tabular-nums">
							{formatDuration(durationMs)}
						</span>
					)}

					{/* Info Popover - Input Parameters */}
					<Popover>
						<PopoverTrigger asChild>
							<Button
								variant="ghost"
								aria-label={`Input parameters for ${resolvedToolCall.name}`}
								size="icon"
								className="size-11 rounded-[var(--bf-radius-control)]"
							>
								<Info className="h-4 w-4 text-muted-foreground" />
							</Button>
						</PopoverTrigger>
						<PopoverContent
							className="max-h-80 w-[min(24rem,calc(100vw-1.5rem))] overflow-auto rounded-[var(--bf-radius-surface)] border-border/70"
							align="end"
						>
							<div className="space-y-2">
								<h4 className="font-medium text-sm">
									Input Parameters
								</h4>
								{resolvedToolCall.arguments &&
								Object.keys(resolvedToolCall.arguments).length >
									0 ? (
									<PrettyInputDisplay
										inputData={
											resolvedToolCall.arguments as Record<
												string,
												unknown
											>
										}
										showToggle={false}
										defaultView="pretty"
									/>
								) : (
									<p className="text-sm text-muted-foreground">
										No input parameters
									</p>
								)}
							</div>
						</PopoverContent>
					</Popover>
				</div>
			</div>

			{executionRemoved && (
				<div className="px-3 pb-3 sm:px-4">
					<RunRemovedNotice variant="inline" />
				</div>
			)}

			{/* Live Logs (while running) */}
			{status === "running" && displayLogs.length > 0 && (
				<div className="border-t border-border/70 bg-muted/10">
					<div ref={logsEndRef} className="max-h-40 space-y-1 overflow-y-auto px-3 py-3">
						{displayLogs.map((log, index) => (
							<p
								key={`${log.timestamp || index}-${index}`}
								className={cn(
									"min-w-0 text-xs font-mono leading-5 [overflow-wrap:anywhere]",
									log.level === "error" &&
										"text-[var(--bf-danger)]",
									log.level === "warning" &&
										"text-[var(--bf-warning)]",
									log.level === "info" &&
										"text-[var(--bf-info)]",
									log.level === "debug" &&
										"text-muted-foreground/70",
								)}
							>
								{log.message}
							</p>
						))}
					</div>
				</div>
			)}

			{/* Single log line when pending (no streaming yet) */}
			{status === "pending" && latestLog && (
				<div className="border-t border-border/70 bg-muted/10 px-3 py-3">
					<p className="text-xs font-mono text-muted-foreground [overflow-wrap:anywhere]">
						{latestLog.message}
					</p>
				</div>
			)}

			{/* Error Message */}
			{(status === "failed" || status === "timeout") && error && (
				<div className="border-t border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)]/50 px-3 py-3">
					<p role="alert" className="text-sm font-mono text-[var(--bf-danger)] [overflow-wrap:anywhere]">
						{error}
					</p>
				</div>
			)}

			{/* Result Section (collapsible) */}
			{isComplete && hasResult && (
				<Collapsible open={isResultOpen} onOpenChange={setIsResultOpen}>
					<CollapsibleTrigger asChild>
						<button
							type="button"
							className="flex min-h-11 w-full items-center gap-2 border-t border-border/70 bg-muted/10 px-3 py-3 text-left motion-safe:transition-colors hover:bg-muted/20 focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2"
						>
							{isResultOpen ? (
								<ChevronDown className="h-4 w-4 text-muted-foreground" />
							) : (
								<ChevronRight className="h-4 w-4 text-muted-foreground" />
							)}
							<span className="text-sm font-medium">Result</span>
						</button>
					</CollapsibleTrigger>
					<AnimatePresence>
						{isResultOpen && (
							<CollapsibleContent forceMount>
									<motion.div
										initial={{ height: 0, opacity: 0 }}
										animate={{ height: "auto", opacity: 1 }}
										exit={{ height: 0, opacity: 0 }}
										transition={{
											duration: prefersReducedMotion ? 0 : 0.2,
										}}
										className="overflow-hidden"
									>
									<div className="border-t border-border/70">
										{/* Result Section */}
										<div className="max-h-64 overflow-auto px-3 py-3">
											{(() => {
												const mcpContent =
													extractMcpContent(result);
												if (mcpContent) {
													return (
														<div className="space-y-2">
															<ToolOutputDisplay
																text={
																	mcpContent.text
																}
															/>
															{mcpContent.structured !==
																undefined &&
																mcpContent.structured !==
																	null && (
																	<details className="text-xs">
																		<summary className="min-h-11 cursor-pointer py-3 text-muted-foreground hover:text-foreground">
																			View
																			raw
																			data
																		</summary>
																		<PrettyInputDisplay
																			inputData={
																				mcpContent.structured as Record<
																					string,
																					unknown
																				>
																			}
																			showToggle={
																				false
																			}
																			defaultView="tree"
																		/>
																	</details>
																)}
														</div>
													);
												}
												// Legacy: render as before
												if (
													typeof result ===
														"object" &&
													result !== null
												) {
													return (
														<PrettyInputDisplay
															inputData={
																result as Record<
																	string,
																	unknown
																>
															}
															showToggle={true}
															defaultView="pretty"
														/>
													);
												}
												return (
													<pre className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap text-muted-foreground leading-6 [overflow-wrap:anywhere]">
														{typeof result ===
														"string"
															? result
															: JSON.stringify(
																	result,
																	null,
																	2
																)}
													</pre>
												);
											})()}
										</div>
										{/* Logs Section */}
										{displayLogs.length > 0 && (
											<div className="border-t border-border/70 px-3 py-3">
												<h5 className="mb-2 text-xs font-medium text-muted-foreground">
													Logs
												</h5>
												<div className="max-h-32 space-y-1 overflow-y-auto">
													{displayLogs.map(
														(log, index) => (
															<p
																key={`${log.timestamp || index}-${index}`}
																className={cn(
																	"min-w-0 text-xs font-mono leading-5 [overflow-wrap:anywhere]",
																	log.level ===
																		"error" &&
																		"text-[var(--bf-danger)]",
																	log.level ===
																		"warning" &&
																		"text-[var(--bf-warning)]",
																	log.level ===
																		"info" &&
																		"text-[var(--bf-info)]",
																	log.level ===
																		"debug" &&
																		"text-muted-foreground/70",
																)}
															>
																{log.message}
															</p>
														),
													)}
												</div>
											</div>
										)}
									</div>
								</motion.div>
							</CollapsibleContent>
						)}
					</AnimatePresence>
				</Collapsible>
			)}
		</div>
	);
}
