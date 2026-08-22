/**
 * ToolExecutionBadge Component
 *
 * Compact SDK tool execution row with inline expandable details.
 *
 * Features:
 * - Status icon (spinner for running, check for success, x for failed)
 * - Tool name
 * - Optional duration
 * - Click to expand full-width details in the conversation flow
 */

import { useState } from "react";
import {
	CheckCircle2,
	XCircle,
	Clock,
	Loader2,
	ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PrettyInputDisplay } from "@/components/execution/PrettyInputDisplay";
import type { components } from "@/lib/v1";
import type {
	ToolExecutionStatus,
	ToolExecutionLog,
} from "./ToolExecutionCard";

type ToolCall = components["schemas"]["ToolCall"];

/** Streaming state for live updates during execution */
export interface StreamingBadgeState {
	status: ToolExecutionStatus;
	logs: ToolExecutionLog[];
	result?: unknown;
	error?: string;
	durationMs?: number;
}

interface ToolExecutionBadgeProps {
	/** Tool call info for display */
	toolCall: ToolCall;
	/** Status from streaming state or saved execution */
	status: ToolExecutionStatus;
	/** Execution result (for inline details) */
	result?: unknown;
	/** Error message if failed */
	error?: string;
	/** Execution duration in milliseconds */
	durationMs?: number;
	/** Logs for popover details */
	logs?: ToolExecutionLog[];
	className?: string;
}

const statusConfig: Record<
	ToolExecutionStatus,
	{
		icon: typeof Clock;
		className: string;
	}
> = {
	pending: {
		icon: Clock,
		className: "text-muted-foreground",
	},
	running: {
		icon: Loader2,
		className: "text-blue-500 animate-spin",
	},
	success: {
		icon: CheckCircle2,
		className: "text-green-500",
	},
	failed: {
		icon: XCircle,
		className: "text-destructive",
	},
	timeout: {
		icon: Clock,
		className: "text-amber-500",
	},
};

export function ToolExecutionBadge({
	toolCall,
	status,
	result,
	error,
	durationMs,
	logs = [],
	className,
}: ToolExecutionBadgeProps) {
	const [isOpen, setIsOpen] = useState(false);

	const config = statusConfig[status];
	const StatusIcon = config.icon;

	// Format duration
	const formatDuration = (ms: number) => {
		if (ms < 1000) return `${ms}ms`;
		return `${(ms / 1000).toFixed(1)}s`;
	};

	const hasDetails =
		result !== undefined ||
		error !== undefined ||
		(toolCall.arguments && Object.keys(toolCall.arguments).length > 0);

	return (
		<div className="w-full min-w-0">
			<button
				type="button"
				disabled={!hasDetails}
				onClick={() => setIsOpen((value) => !value)}
				aria-expanded={hasDetails ? isOpen : undefined}
				className={cn(
					"flex min-h-11 w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs text-muted-foreground outline-none transition-colors hover:bg-muted/50 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none motion-reduce:transition-none sm:min-h-7",
					status === "failed" && "text-destructive",
					status === "timeout" && "text-amber-600 dark:text-amber-400",
					className,
				)}
			>
				<StatusIcon className={cn("h-3 w-3 shrink-0", config.className)} />
				<span className="font-medium">{toolCall.name}</span>
				{durationMs !== undefined && (
					<span className="text-muted-foreground">
						{formatDuration(durationMs)}
					</span>
				)}
				{hasDetails && (
					<ChevronDown
						className={cn(
							"ml-0.5 h-3 w-3 text-muted-foreground transition-transform motion-reduce:transition-none",
							isOpen && "rotate-180",
						)}
					/>
				)}
			</button>

			{hasDetails && isOpen && (
				<div className="mt-1 w-full overflow-hidden rounded-xl border border-border bg-muted/20 text-foreground">
					<div className="max-h-[28rem] w-full space-y-4 overflow-auto p-4">
						{/* Input Parameters */}
						{toolCall.arguments &&
							Object.keys(toolCall.arguments).length > 0 && (
								<div>
									<h4 className="text-xs font-medium text-muted-foreground mb-1">
										Input
									</h4>
									<PrettyInputDisplay
										inputData={
											toolCall.arguments as Record<
												string,
												unknown
											>
										}
										showToggle={false}
										defaultView="pretty"
									/>
								</div>
							)}

						{/* Error */}
						{error && (
							<div>
								<h4 className="text-xs font-medium text-destructive mb-1">
									Error
								</h4>
								<pre className="text-xs font-mono text-destructive whitespace-pre-wrap">
									{error}
								</pre>
							</div>
						)}

						{/* Result */}
						{result !== undefined && !error && (
							<div>
								<h4 className="text-xs font-medium text-muted-foreground mb-1">
									Result
								</h4>
								{typeof result === "object" &&
								result !== null ? (
									<PrettyInputDisplay
										inputData={
											result as Record<string, unknown>
										}
										showToggle={false}
										defaultView="pretty"
									/>
								) : (
									<pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground">
										{typeof result === "string"
											? result
											: JSON.stringify(result, null, 2)}
									</pre>
								)}
							</div>
						)}

						{/* Logs */}
						{logs.length > 0 && (
							<div>
								<h4 className="text-xs font-medium text-muted-foreground mb-1">
									Logs
								</h4>
								<div className="max-h-24 overflow-y-auto space-y-0.5">
									{logs.map((log, index) => (
										<p
											key={`${log.timestamp || index}-${index}`}
											className={cn(
												"text-xs font-mono",
												log.level === "error" &&
													"text-destructive",
												log.level === "warning" &&
													"text-amber-500",
												log.level === "info" &&
													"text-muted-foreground",
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
					</div>
				</div>
			)}
		</div>
	);
}
