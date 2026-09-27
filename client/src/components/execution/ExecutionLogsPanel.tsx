import { LogEntryRow } from "./LogEntryRow";
import {
	useRef,
	useEffect,
	useCallback,
	useMemo,
	useState,
	type ReactNode,
} from "react";
import { ArrowDown, ArrowUp, Check, Copy, Download, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { endOfDay, startOfDay } from "date-fns";
import type { DateRange } from "react-day-picker";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { copyToClipboard } from "@/lib/clipboard";
import { toast } from "sonner";
import type { components } from "@/lib/v1";
import type { StreamingLog } from "@/stores/executionStreamStore";

// Re-export from generated types
export type ExecutionStatus =
	components["schemas"]["ExecutionStatus"] | "Cancelling" | "Cancelled";
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
	/** Larger, page-primary rendering with full controls. */
	variant?: "default" | "primary";
	/**
	 * Optional date-range window (day granularity). Rendered as a native
	 * picker beside the level filter when `showDateFilter` is set; applied
	 * locally here, server-side by consumers that paginate (same contract).
	 */
	dateRange?: DateRange | undefined;
	onDateRangeChange?: (range: DateRange | undefined) => void;
	showDateFilter?: boolean;
	/**
	 * Display order. `asc` (default) renders oldest-first and follows
	 * the bottom edge; `desc` renders newest-first and follows the top
	 * edge. Consumers pass chronologically-ordered logs either way.
	 */
	sortOrder?: "asc" | "desc";
	/**
	 * Pagination node rendered in the persistent bottom bar (border-t)
	 * next to the follow control — e.g. a line count plus a
	 * load-older button. The bar renders while logs exist, so paging
	 * never shifts layout. Without it, the follow control floats over
	 * the list only while following is paused.
	 */
	pagination?: ReactNode;
}

const levelColors: Record<string, string> = {
	debug: "text-muted-foreground",
	info: "text-[var(--bf-info)]",
	warning: "text-[var(--bf-warning)]",
	error: "text-[var(--bf-danger)]",
	traceback: "text-[var(--bf-danger)]",
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
	isConnected,
	isLoading = false,
	isPlatformAdmin = false,
	className,
	maxHeight = "600px",
	variant = "default",
	dateRange,
	onDateRangeChange,
	showDateFilter = false,
	sortOrder = "asc",
	pagination,
}: ExecutionLogsPanelProps) {
	const logsContainerRef = useRef<HTMLDivElement>(null);
	const [autoScroll, setAutoScroll] = useState(true);
	const [copied, setCopied] = useState(false);
	const [query, setQuery] = useState("");
	const [levelFilter, setLevelFilter] = useState("all");

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
	const visibleLogs = useMemo(() => {
		const normalizedQuery = query.trim().toLowerCase();
		return logs.filter((log) => {
			const level = (log.level || "info").toLowerCase();
			if (levelFilter !== "all" && level !== levelFilter) return false;
			const at = Date.parse(log.timestamp || "");
			if (
				dateRange?.from &&
				!(at >= startOfDay(dateRange.from).getTime())
			)
				return false;
			if (dateRange?.to && !(at <= endOfDay(dateRange.to).getTime()))
				return false;
			if (!normalizedQuery) return true;
			const data =
				"data" in log && log.data ? JSON.stringify(log.data) : "";
			return `${log.message || ""} ${level} ${data}`
				.toLowerCase()
				.includes(normalizedQuery);
		});
	}, [logs, query, levelFilter, dateRange]);

	const orderedLogs = useMemo(
		() =>
			sortOrder === "desc" ? [...visibleLogs].reverse() : visibleLogs,
		[visibleLogs, sortOrder],
	);

	const renderItems = useMemo(
		() => coalesceTracebacks(orderedLogs),
		[orderedLogs],
	);

	// Auto-scroll to the follow edge when new logs arrive while following:
	// bottom for chronological feeds, top for newest-first feeds.
	useEffect(() => {
		const container = logsContainerRef.current;
		if (autoScroll && container && visibleLogs.length > 0) {
			container.scrollTop =
				sortOrder === "desc" ? 0 : container.scrollHeight;
		}
	}, [visibleLogs.length, autoScroll, sortOrder]);

	// Handle scroll to detect if user has scrolled away from the follow
	// edge (pause auto-scroll)
	const handleLogsScroll = useCallback(() => {
		const container = logsContainerRef.current;
		if (!container) return;

		// Check if at the follow edge (with 50px threshold)
		const isFollowing =
			sortOrder === "desc"
				? container.scrollTop < 50
				: container.scrollHeight -
						container.scrollTop -
						container.clientHeight <
					50;
		setAutoScroll(isFollowing);
	}, [sortOrder]);

	const handleCopyLogs = useCallback(async () => {
		const text = orderedLogs
			.map((log) => {
				const time = formatLogTime(log.timestamp);
				const level = (log.level || "INFO").toUpperCase();
				return `${time}  ${level}  ${log.message || ""}`;
			})
			.join("\n");
		const copied = await copyToClipboard(text);
		if (copied) {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		} else {
			toast.error("Failed to copy logs");
		}
	}, [orderedLogs]);

	const handleDownloadLogs = useCallback(() => {
		const text = logs
			.map((log) => {
				const time = formatLogTime(log.timestamp);
				const level = (log.level || "INFO").toUpperCase();
				const data =
					"data" in log && log.data
						? ` ${JSON.stringify(log.data)}`
						: "";
				return `${time}  ${level}  ${log.message || ""}${data}`;
			})
			.join("\n");
		const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = "execution-logs.txt";
		link.click();
		URL.revokeObjectURL(url);
	}, [logs]);

	const copyButton = logs.length > 0 && (
		<Button
			variant="ghost"
			size="icon-lg"
			onClick={handleCopyLogs}
			title="Copy logs"
			aria-label={copied ? "Logs copied" : "Copy logs"}
		>
			{copied ? (
				<Check className="h-3.5 w-3.5 text-[var(--bf-success)]" />
			) : (
				<Copy className="h-3.5 w-3.5" />
			)}
		</Button>
	);

	const renderItem = (item: LogRenderItem, index: number) => {
		if (item.kind === "traceback") {
			return (
				<LogEntryRow
					level="traceback"
					key={index}
					className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-b border-border/50 px-3 py-3 font-mono text-xs last:border-0 @3xl:grid-cols-[auto_78px_minmax(0,1fr)] @3xl:py-2 hover:bg-muted/40"
					data-testid="log-traceback-block"
				>
					<span className="whitespace-nowrap tabular-nums text-muted-foreground">
						{formatLogTime(item.timestamp)}
					</span>
					<span
						className={`min-w-0 font-semibold uppercase ${levelColors.traceback}`}
					>
						traceback
					</span>
					<pre className="col-span-full min-w-0 whitespace-pre-wrap text-sm leading-relaxed [overflow-wrap:anywhere] @3xl:col-span-1">
						{item.lines.join("\n")}
					</pre>
				</LogEntryRow>
			);
		}

		const log = item.log;
		const level = log.level?.toLowerCase() || "info";
		const levelColor = levelColors[level] || "text-muted-foreground";
		// data field only exists on ExecutionLogPublic, not StreamingLog
		const data = "data" in log ? log.data : undefined;

		return (
			<LogEntryRow
				level={log.level}
				key={index}
				className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-b border-border/50 px-3 py-3 font-mono text-xs last:border-0 @3xl:grid-cols-[auto_78px_minmax(0,1fr)] @3xl:py-2 hover:bg-muted/40"
			>
				<span className="whitespace-nowrap tabular-nums text-muted-foreground">
					{formatLogTime(log.timestamp)}
				</span>
				<span
					className={`min-w-0 font-semibold uppercase ${levelColor}`}
				>
					{log.level}
				</span>
				<span
					data-testid="log-message"
					className="col-span-full min-w-0 whitespace-pre-wrap text-sm leading-relaxed [overflow-wrap:anywhere] @3xl:col-span-1"
				>
					{log.message}
				</span>
				{data && Object.keys(data).length > 0 && (
					<details className="col-span-full min-w-0 text-xs @3xl:col-start-3">
						<summary className="min-h-11 cursor-pointer content-center text-muted-foreground">
							data
						</summary>
						<pre className="mt-1 min-w-0 whitespace-pre-wrap rounded-[var(--bf-radius-control)] bg-muted p-2 [overflow-wrap:anywhere]">
							{JSON.stringify(data, null, 2)}
						</pre>
					</details>
				)}
			</LogEntryRow>
		);
	};

	const renderLogList = () => (
		<div
			ref={logsContainerRef}
			onScroll={handleLogsScroll}
			className="min-h-0 min-w-0 overflow-y-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
			aria-label="Execution log messages"
			role="region"
			tabIndex={0}
			style={{
				maxHeight: `var(--execution-log-max-height, ${maxHeight})`,
			}}
		>
			{renderItems.map(renderItem)}
		</div>
	);

	const lineCount = logs.length;
	const visibleCount = visibleLogs.length;
	const countLabel = `${lineCount} line${lineCount !== 1 ? "s" : ""}`;

	// Resume following at the follow edge (top for newest-first, bottom
	// otherwise) — shared by the bottom bar and the floating control.
	const resumeFollowing = useCallback(() => {
		setAutoScroll(true);
		const container = logsContainerRef.current;
		if (container)
			container.scrollTop =
				sortOrder === "desc" ? 0 : container.scrollHeight;
	}, [sortOrder]);

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
			<div
				role="status"
				aria-label="Loading execution logs"
				className="space-y-2 p-4"
			>
				<Skeleton className="h-3 w-full" />
				<Skeleton className="h-3 w-5/6" />
				<Skeleton className="h-3 w-4/5" />
			</div>
		) : visibleLogs.length === 0 ? (
			<div className="space-y-3 px-4 py-8 text-center text-sm text-muted-foreground">
				<p>No logs match the current filters</p>
				<Button
					variant="outline"
					className="min-h-11"
					onClick={() => {
						setQuery("");
						setLevelFilter("all");
						onDateRangeChange?.(undefined);
					}}
				>
					Clear filters
				</Button>
			</div>
		) : (
			renderLogList()
		);

	return (
		<div
			className={cn(
				"@container relative min-w-0 overflow-hidden rounded-[var(--bf-radius-surface)] border border-border bg-muted/50",
				variant === "primary" &&
					"bg-background xl:flex xl:min-h-0 xl:flex-1 xl:flex-col xl:[--execution-log-max-height:none]",
				className,
			)}
		>
			{/* Header band (step-2) */}
			<div className="shrink-0 space-y-3 border-b border-border/50 bg-muted px-3 py-3">
				<div className="flex items-center justify-between gap-3">
					<div className="flex min-w-0 flex-wrap items-center gap-2">
						<span className="text-sm font-semibold">Logs</span>
						{lineCount > 0 && (
							<span className="text-xs text-muted-foreground">
								{countLabel}
								{visibleCount !== lineCount &&
									` · ${visibleCount} visible`}
								{!isPlatformAdmin && " · INFO and above"}
							</span>
						)}
						{isConnected && isRunning && (
							<Badge
								variant="secondary"
								className="gap-1 text-xs"
							>
								<span
									data-testid="execution-live-indicator"
									aria-hidden="true"
									className="h-2 w-2 rounded-full bg-[image:var(--bf-activity-gradient)] motion-safe:animate-pulse motion-reduce:animate-none"
								/>
								Live
							</Badge>
						)}
					</div>
					<div className="flex shrink-0 items-center gap-1">
						{copyButton}
						{logs.length > 0 && (
							<Button
								variant="ghost"
								size="icon-lg"
								onClick={handleDownloadLogs}
								title="Download all logs"
								aria-label="Download all logs"
							>
								<Download className="h-3.5 w-3.5" />
							</Button>
						)}
					</div>
				</div>
				{logs.length > 0 && (
					<div
						className={
							showDateFilter
								? "grid gap-2 @lg:grid-cols-[minmax(0,1fr)_9rem_auto]"
								: "grid gap-2 @lg:grid-cols-[minmax(0,1fr)_9rem]"
						}
					>
						<div className="relative min-w-0">
							<Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
							<Input
								value={query}
								onChange={(event) =>
									setQuery(event.target.value)
								}
								className="pl-9 pr-11"
								placeholder="Search logs..."
								aria-label="Search logs"
							/>
							{query && (
								<span className="absolute inset-y-0 right-1 z-20 flex items-center">
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="size-8"
										onClick={() => setQuery("")}
										aria-label="Clear log search"
									>
										<X className="size-4" />
									</Button>
								</span>
							)}
						</div>
						<Select
							value={levelFilter}
							onValueChange={setLevelFilter}
						>
							<SelectTrigger
								aria-label="Log level"
								className="h-10 w-full"
							>
								<SelectValue placeholder="All levels" />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="all">All levels</SelectItem>
								<SelectItem value="info">Info</SelectItem>
								<SelectItem value="warning">Warning</SelectItem>
								<SelectItem value="error">Error</SelectItem>
								<SelectItem value="traceback">
									Traceback
								</SelectItem>
							{isPlatformAdmin && (
								<SelectItem value="debug">Debug</SelectItem>
							)}
							</SelectContent>
						</Select>
						{showDateFilter && onDateRangeChange && (
							<DateRangePicker
								dateRange={dateRange}
								onDateRangeChange={onDateRangeChange}
							/>
						)}
					</div>
				)}
			</div>
			{isRunning && isConnected === false && (
				<p
					role="status"
					className="border-b border-border/50 bg-[var(--bf-warning-soft)] px-3 py-3 text-sm text-foreground"
				>
					Live connection unavailable. New log messages may be
					delayed.
				</p>
			)}
			{/* Content */}
			{logsContent}
			{pagination && logs.length > 0 && (
				<div className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-border bg-muted px-3 py-2">
					<div className="flex min-h-11 items-center gap-1.5 text-sm text-muted-foreground">
						{!autoScroll ? (
							<Button
								variant="outline"
								className="min-h-11"
								onClick={resumeFollowing}
							>
								{sortOrder === "desc" ? (
									<ArrowUp
										aria-hidden="true"
										className="h-4 w-4"
									/>
								) : (
									<ArrowDown
										aria-hidden="true"
										className="h-4 w-4"
									/>
								)}
								{sortOrder === "desc"
									? "Back to Top"
									: "Jump to latest"}
							</Button>
						) : isConnected ? (
							<span className="inline-flex items-center gap-1.5">
								<span
									aria-hidden="true"
									className="h-2 w-2 rounded-full bg-[image:var(--bf-activity-gradient)] motion-safe:animate-pulse motion-reduce:animate-none"
								/>
								Following live updates
							</span>
						) : (
							<span>Live updates unavailable</span>
						)}
					</div>
					{pagination}
				</div>
			)}
			{!pagination && !autoScroll && visibleLogs.length > 0 && (
				<div className="absolute inset-x-0 bottom-0 z-10 flex flex-wrap items-center justify-between gap-2 border-t border-border bg-muted px-3 py-2">
					<span
						role="status"
						className="text-xs text-muted-foreground"
					>
						Following paused
					</span>
					<Button
						variant="outline"
						className="min-h-11"
						onClick={resumeFollowing}
					>
						<ArrowDown className="h-4 w-4" />
						Jump to latest
					</Button>
				</div>
			)}
		</div>
	);
}
