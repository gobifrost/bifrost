import { useRef, useEffect, useCallback, useMemo, useState } from "react";
import { Loader2, Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { components } from "@/lib/v1";
import type { StreamingLog } from "@/stores/executionStreamStore";

// Re-export from generated types
export type ExecutionStatus = components["schemas"]["ExecutionStatus"];
export type ExecutionLogPublic = components["schemas"]["ExecutionLogPublic"];

// Union type that handles both API logs and streaming logs
export type LogEntry = ExecutionLogPublic | StreamingLog;

interface ExecutionLogsPanelProps {
	/** Logs to display (already merged/deduped by parent) */
	logs?: LogEntry[];
	/** Current execution status */
	status?: ExecutionStatus;
	/** Whether the WebSocket connection is active */
	isConnected?: boolean;
	/** Whether logs are still loading from API */
	isLoading?: boolean;
	/** Whether the user is a platform admin (shows DEBUG logs) */
	isPlatformAdmin?: boolean;
	/** Optional className for the panel */
	className?: string;
	/** Maximum height for the logs container */
	maxHeight?: string;
}

const levelColors: Record<string, string> = {
	debug: "text-muted-foreground",
	info: "text-blue-600 dark:text-blue-400",
	warning: "text-yellow-600 dark:text-yellow-500",
	error: "text-red-600 dark:text-red-400",
	traceback: "text-orange-600 dark:text-orange-400",
};

/**
 * A renderable unit: either a single log line, or a run of consecutive
 * TRACEBACK lines coalesced into one block. A Python traceback arrives
 * as N separate log entries; rendering it as N rows (each repeating
 * timestamp + level) destroys scannability — it is ONE artifact.
 */
type LogRenderItem =
	| { kind: "line"; log: LogEntry }
	| { kind: "traceback"; timestamp?: string | null; lines: string[] };

export function coalesceTracebacks(logs: LogEntry[]): LogRenderItem[] {
	const items: LogRenderItem[] = [];
	for (const log of logs) {
		const isTraceback = log.level?.toLowerCase() === "traceback";
		const last = items[items.length - 1];
		if (isTraceback && last?.kind === "traceback") {
			last.lines.push(log.message || "");
		} else if (isTraceback) {
			items.push({
				kind: "traceback",
				timestamp: log.timestamp,
				lines: [log.message || ""],
			});
		} else {
			items.push({ kind: "line", log });
		}
	}
	return items;
}

function formatLogTime(timestamp?: string | null): string {
	return timestamp ? new Date(timestamp).toLocaleTimeString() : "";
}

export function ExecutionLogsPanel({
	logs = [],
	status,
	isConnected = false,
	isLoading = false,
	isPlatformAdmin = false,
	className,
	maxHeight = "600px",
}: ExecutionLogsPanelProps) {
	const logsContainerRef = useRef<HTMLDivElement>(null);
	const [autoScroll, setAutoScroll] = useState(true);
	const [copied, setCopied] = useState(false);

	const isRunning =
		status === "Running" || status === "Pending" || status === "Cancelling";
	const isComplete =
		status === "Success" ||
		status === "Failed" ||
		status === "CompletedWithErrors" ||
		status === "Timeout" ||
		status === "Cancelled";

	// Parent (ExecutionDetails) already merges API + streaming logs via
	// mergeLogsWithDedup and memoizes the array, so `logs` is a stable input.
	const renderItems = useMemo(() => coalesceTracebacks(logs), [logs]);

	// Auto-scroll to bottom when new logs arrive.
	// Uses scrollTop instead of scrollIntoView to avoid scrolling the outer page.
	useEffect(() => {
		const container = logsContainerRef.current;
		if (autoScroll && container && logs.length > 0) {
			container.scrollTop = container.scrollHeight;
		}
	}, [logs.length, autoScroll]);

	// Handle scroll to detect if user has scrolled up (pause auto-scroll)
	const handleLogsScroll = useCallback(() => {
		const container = logsContainerRef.current;
		if (!container) return;

		// Check if scrolled to bottom (with 50px threshold)
		const isAtBottom =
			container.scrollHeight -
				container.scrollTop -
				container.clientHeight <
			50;
		setAutoScroll(isAtBottom);
	}, []);

	const handleCopyLogs = useCallback(() => {
		const text = logs
			.map((log) => {
				const time = formatLogTime(log.timestamp);
				const level = (log.level || "INFO").toUpperCase();
				return `${time}  ${level}  ${log.message || ""}`;
			})
			.join("\n");
		navigator.clipboard.writeText(text).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		});
	}, [logs]);

	const copyButton = logs.length > 0 && (
		<Button
			variant="ghost"
			size="icon"
			className="h-7 w-7"
			onClick={handleCopyLogs}
			title="Copy logs"
		>
			{copied ? (
				<Check className="h-3.5 w-3.5 text-green-500" />
			) : (
				<Copy className="h-3.5 w-3.5" />
			)}
		</Button>
	);

	const renderItem = (item: LogRenderItem, index: number) => {
		if (item.kind === "traceback") {
			return (
				<div
					key={index}
					className="flex gap-3 rounded px-2 py-1 text-xs font-mono hover:bg-muted/40"
					data-testid="log-traceback-block"
				>
					<span className="whitespace-nowrap tabular-nums text-muted-foreground">
						{formatLogTime(item.timestamp)}
					</span>
					<span
						className={`min-w-[70px] font-semibold uppercase ${levelColors.traceback}`}
					>
						traceback
					</span>
					<pre className="flex-1 whitespace-pre-wrap break-words">
						{item.lines.join("\n")}
					</pre>
				</div>
			);
		}

		const log = item.log;
		const level = log.level?.toLowerCase() || "info";
		const levelColor = levelColors[level] || "text-muted-foreground";
		// data field only exists on ExecutionLogPublic, not StreamingLog
		const data = "data" in log ? log.data : undefined;

		return (
			<div
				key={index}
				className="flex gap-3 rounded px-2 py-1 text-xs font-mono hover:bg-muted/40"
			>
				<span className="whitespace-nowrap tabular-nums text-muted-foreground">
					{formatLogTime(log.timestamp)}
				</span>
				<span
					className={`min-w-[70px] font-semibold uppercase ${levelColor}`}
				>
					{log.level}
				</span>
				<span className="flex-1 whitespace-pre-wrap break-words">
					{log.message}
				</span>
				{data && Object.keys(data).length > 0 && (
					<details className="text-xs">
						<summary className="cursor-pointer text-muted-foreground">
							data
						</summary>
						<pre className="mt-1 p-2 rounded-md bg-muted">
							{JSON.stringify(data, null, 2)}
						</pre>
					</details>
				)}
			</div>
		);
	};

	const renderLogList = () => (
		<div
			ref={logsContainerRef}
			onScroll={handleLogsScroll}
			className="overflow-y-auto py-1"
			style={{ maxHeight }}
		>
			{renderItems.map(renderItem)}
		</div>
	);

	const lineCount = logs.length;
	const countLabel = `${lineCount} line${lineCount !== 1 ? "s" : ""}`;

	// Inspector panel: one step-1 surface framed by a hairline ring, with a
	// step-2 header band — same idiom in the drawer and the details page.
	const logsContent =
		logs.length === 0 && !isLoading ? (
			<div className="text-center text-sm text-muted-foreground py-8">
				{isRunning
					? "Waiting for logs..."
					: isComplete
						? "No logs captured"
						: "No execution in progress"}
			</div>
		) : isLoading && logs.length === 0 ? (
			<div className="space-y-2 p-4">
				<Skeleton className="h-3 w-full" />
				<Skeleton className="h-3 w-5/6" />
				<Skeleton className="h-3 w-4/5" />
			</div>
		) : (
			renderLogList()
		);

	return (
		<div
			className={cn(
				"overflow-hidden rounded-lg bg-muted/50 ring-1 ring-foreground/5",
				className,
			)}
		>
			{/* Header band (step-2) */}
			<div className="flex items-center justify-between border-b border-border/50 bg-muted px-3 py-1.5">
				<div className="flex items-center gap-2">
					<span className="text-sm font-medium">Logs</span>
					{lineCount > 0 && (
						<span className="text-xs text-muted-foreground">
							{countLabel}
							{!isPlatformAdmin && " · INFO and above"}
						</span>
					)}
					{isConnected && isRunning && (
						<Badge variant="secondary" className="text-xs">
							<Loader2 className="mr-1 h-3 w-3 animate-spin" />
							Live
						</Badge>
					)}
				</div>
				{copyButton}
			</div>
			{/* Content */}
			{logsContent}
		</div>
	);
}
