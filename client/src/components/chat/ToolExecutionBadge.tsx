/**
 * ToolExecutionBadge Component
 *
 * Compact inline badge for displaying SDK tool execution status.
 * Designed to flow horizontally and take minimal space.
 *
 * Features:
 * - Status icon (spinner for running, check for success, x for failed)
 * - Tool name
 * - Optional duration
 * - Click to expand details in a popover
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
import { Badge } from "@/components/ui/badge";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
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
	/** Execution result (for popover details) */
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
		badgeClassName: string;
	}
> = {
	pending: {
		icon: Clock,
		className: "text-muted-foreground",
		badgeClassName:
			"bg-muted text-muted-foreground hover:bg-muted/80 border-muted-foreground/20",
	},
	running: {
		icon: Loader2,
		className: "text-blue-500 animate-spin",
		badgeClassName:
			"bg-blue-500/10 text-blue-600 hover:bg-blue-500/20 border-blue-500/30 animate-pulse",
	},
	success: {
		icon: CheckCircle2,
		className: "text-green-500",
		badgeClassName:
			"bg-green-500/10 text-green-600 hover:bg-green-500/20 border-green-500/30",
	},
	failed: {
		icon: XCircle,
		className: "text-destructive",
		badgeClassName:
			"bg-destructive/10 text-destructive hover:bg-destructive/20 border-destructive/30",
	},
	timeout: {
		icon: Clock,
		className: "text-amber-500",
		badgeClassName:
			"bg-amber-500/10 text-amber-600 hover:bg-amber-500/20 border-amber-500/30",
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
		<Popover open={isOpen} onOpenChange={setIsOpen}>
			<PopoverTrigger asChild>
				<Badge
					variant="outline"
					className={cn(
						"cursor-pointer gap-1.5 px-2 py-1 text-xs font-normal transition-colors",
						config.badgeClassName,
						className,
					)}
				>
					<StatusIcon className={cn("h-3 w-3", config.className)} />
					<span className="font-medium">{toolCall.name}</span>
					{durationMs !== undefined && (
						<span className="text-muted-foreground">
							{formatDuration(durationMs)}
						</span>
					)}
					{hasDetails && (
						<ChevronDown
							className={cn(
								"h-3 w-3 text-muted-foreground transition-transform",
								isOpen && "rotate-180",
							)}
						/>
					)}
				</Badge>
			</PopoverTrigger>

			{hasDetails && (
				<PopoverContent
					className="w-96 max-h-80 overflow-auto"
					align="start"
				>
					<div className="space-y-3">
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
				</PopoverContent>
			)}
		</Popover>
	);
}
