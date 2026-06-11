import { useRef, useEffect, useCallback, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
	/** Optional className for the card */
	className?: string;
	/** Maximum height for the logs container */
	maxHeight?: string;
	/** Render without Card wrapper (for embedding in other panels) */
	embedded?: boolean;
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
	embedded = false,
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

	// Parent (ExecutionDetails) already merges API + streaming logs via mergeLogsWithDedup
	const displayLogs = useMemo(() => logs, [logs]);
	const renderItems = useMemo(
		() => coalesceTracebacks(displayLogs),
		[displayLogs],
	);

	// Auto-scroll to bottom when new logs arrive.
	// Uses scrollTop instead of scrollIntoView to avoid scrolling the outer page.
	useEffect(() => {
		const container = logsContainerRef.current;
		if (autoScroll && container && displayLogs.length > 0) {
			container.scrollTop = container.scrollHeight;
		}
	}, [displayLogs.length, autoScroll]);

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
		const text = displayLogs
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
	}, [displayLogs]);

	const copyButton = displayLogs.length > 0 && (
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
						<pre className="mt-1 p-2 bg-muted rounded">
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

	const lineCount = displayLogs.length;
	const countLabel = `${lineCount} line${lineCount !== 1 ? "s" : ""}`;

	// Embedded content (no Card wrapper)
	const renderEmbeddedContent = () => {
		const logsContent =
			displayLogs.length === 0 && !isLoading ? (
				<div className="text-center text-sm text-muted-foreground py-8">
					{isRunning
						? "Waiting for logs..."
						: isComplete
							? "No logs captured"
							: "No execution in progress"}
				</div>
			) : isLoading && displayLogs.length === 0 ? (
				<div className="space-y-2 p-4">
					<Skeleton className="h-3 w-full" />
					<Skeleton className="h-3 w-5/6" />
					<Skeleton className="h-3 w-4/5" />
				</div>
			) : (
				renderLogList()
			);

		return (
			<div className={className}>
				{/* Header */}
				<div className="flex items-center justify-between border-b bg-muted/30 px-4 py-1.5">
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
	};

	// Use embedded rendering if requested
	if (embedded) {
		return renderEmbeddedContent();
	}

	// Running state - show logs as they stream in
	if (isRunning) {
		return (
			<Card className={className}>
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<CardTitle className="flex items-center gap-2">
							Logs
							{lineCount > 0 && (
								<span className="text-xs font-normal text-muted-foreground">
									{countLabel}
									{!isPlatformAdmin && " · INFO and above"}
								</span>
							)}
							{isConnected && (
								<Badge variant="secondary" className="text-xs">
									<Loader2 className="mr-1 h-3 w-3 animate-spin" />
									Live
								</Badge>
							)}
						</CardTitle>
						{copyButton}
					</div>
				</CardHeader>
				<CardContent>
					{displayLogs.length === 0 && !isLoading ? (
						<div className="text-center text-sm text-muted-foreground py-8">
							Waiting for logs...
						</div>
					) : isLoading && displayLogs.length === 0 ? (
						<div className="space-y-2">
							<Skeleton className="h-3 w-full" />
							<Skeleton className="h-3 w-5/6" />
							<Skeleton className="h-3 w-4/5" />
						</div>
					) : (
						renderLogList()
					)}
				</CardContent>
			</Card>
		);
	}

	// Complete state - show persisted logs
	if (isComplete) {
		return (
			<Card className={className}>
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<CardTitle className="flex items-center gap-2">
							Logs
							{lineCount > 0 && (
								<span className="text-xs font-normal text-muted-foreground">
									{countLabel}
									{!isPlatformAdmin && " · INFO and above"}
								</span>
							)}
						</CardTitle>
						{copyButton}
					</div>
				</CardHeader>
				<CardContent>
					<AnimatePresence mode="wait">
						{isLoading ? (
							<motion.div
								key="loading"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
								className="space-y-2"
							>
								<Skeleton className="h-4 w-full" />
								<Skeleton className="h-4 w-5/6" />
								<Skeleton className="h-4 w-4/5" />
							</motion.div>
						) : logs.length === 0 ? (
							<motion.div
								key="empty"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
								className="text-center text-sm text-muted-foreground py-8"
							>
								No logs captured
							</motion.div>
						) : (
							<motion.div
								key="content"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2 }}
							>
								{renderLogList()}
							</motion.div>
						)}
					</AnimatePresence>
				</CardContent>
			</Card>
		);
	}

	// Default/unknown state
	return (
		<Card className={className}>
			<CardHeader className="pb-3">
				<CardTitle>Logs</CardTitle>
			</CardHeader>
			<CardContent>
				<div className="text-center text-sm text-muted-foreground py-8">
					No execution in progress
				</div>
			</CardContent>
		</Card>
	);
}
