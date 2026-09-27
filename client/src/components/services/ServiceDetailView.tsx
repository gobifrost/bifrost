import { useMemo } from "react";
import { endOfDay, startOfDay } from "date-fns";
import type { DateRange } from "react-day-picker";
import {
	ArrowLeft,
	Code2,
	Loader2,
	Play,
	Power,
	PowerOff,
	RefreshCw,
	RotateCcw,
	Server,
	Square,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	ExecutionLogsPanel,
	type LogEntry,
} from "@/components/execution/ExecutionLogsPanel";
import type { StreamingLog } from "@/stores/executionStreamStore";
import type {
	ServiceAction,
	ServiceAttempt,
	ServiceListItem,
	ServiceLog,
} from "@/services/services";
import {
	formatServiceUptime,
	formatServiceMemory,
	RESTART_WORDS,
	STARTUP_WORDS,
	formatObservedState,
} from "./ServiceListSurface";

export interface ServiceDetailViewProps {
	service: ServiceListItem;
	/** Live attempt history (newest-first from the API). */
	attempts: ServiceAttempt[];
	/**
	 * Persisted log output (chronological from `GET .../logs`).
	 * Merged with the attempt system rows below; live tail arrives
	 * over the service WebSocket channel in 2.4.
	 */
	logEntries: ServiceLog[];
	/** Total persisted lines (for the showing-N-of-M pagination label). */
	logsTotal: number;
	hasOlderLogs: boolean;
	isLoadingOlderLogs: boolean;
	onLoadOlderLogs: () => void;
	isPlatformAdmin?: boolean;
	onAction?: (service: ServiceListItem, action: ServiceAction) => void;
	onOpenInEditor?: (service: ServiceListItem) => void;
	onBack?: () => void;
	onRefresh?: () => void;
	isRefreshing?: boolean;
	/** Date window: passed to the logs query (page) AND the panel. */
	dateRange: DateRange | undefined;
	onDateRangeChange: (range: DateRange | undefined) => void;
	/** Live tail from `service:{id}` (2.4); merged below, deduped. */
	streamingLogs: StreamingLog[];
	isStreamConnected: boolean;
}

export interface ServiceLogFilter {
	attemptId: string;
	level: string;
	search: string;
	dateRange: DateRange | undefined;
}

/**
 * One timeline row. Attempt lifecycle events (start/ready/stop/exit) are
 * first-class system rows synthesized from live attempts; the 2.3 log
 * endpoint appends user log output into this same shape.
 */
export interface ServiceTimelineRow {
	sequence: string;
	attempt_id: string | null;
	level: string;
	message: string;
	timestamp: string;
	system: boolean;
}

/**
 * Synthesize system rows from live attempts, oldest first (the panel
 * renders in given order and auto-scrolls to the newest).
 */
export function attemptsToTimelineRows(
	attempts: ServiceAttempt[],
): ServiceTimelineRow[] {
	const chronological = [...attempts].sort(
		(a, b) =>
			Date.parse(a.created_at) - Date.parse(b.created_at) ||
			a.id.localeCompare(b.id),
	);
	const rows: ServiceTimelineRow[] = [];
	for (const attempt of chronological) {
		const workerSuffix = attempt.worker_id
			? ` on ${attempt.worker_id}`
			: "";
		const revisionSuffix = attempt.revision
			? ` (revision ${attempt.revision.slice(0, 12)})`
			: "";
		rows.push({
			sequence: `${attempt.id}:started`,
			attempt_id: attempt.id,
			level: "info",
			message: `Attempt #${attempt.restart_number + 1} started${workerSuffix}${revisionSuffix}`,
			timestamp: attempt.created_at,
			system: true,
		});
		if (attempt.ready_at) {
			rows.push({
				sequence: `${attempt.id}:ready`,
				attempt_id: attempt.id,
				level: "info",
				message: "Service reported ready",
				timestamp: attempt.ready_at,
				system: true,
			});
		}
		if (attempt.stop_requested_at) {
			rows.push({
				sequence: `${attempt.id}:stop-requested`,
				attempt_id: attempt.id,
				level: "warning",
				message: "Stop requested",
				timestamp: attempt.stop_requested_at,
				system: true,
			});
		}
		if (attempt.stopped_at) {
			const failed = attempt.state === "failed";
			const reason = attempt.exit_reason ?? attempt.state;
			rows.push({
				sequence: `${attempt.id}:exited`,
				attempt_id: attempt.id,
				level: failed ? "error" : "info",
				message: `Attempt exited (${reason})${attempt.error && failed ? `: ${attempt.error}` : ""}`,
				timestamp: attempt.stopped_at,
				system: true,
			});
		}
	}
	return rows;
}

/**
 * Persisted log output as timeline rows (non-system, chronological
 * from the endpoint). Merged with the attempt rows below.
 */
export function serviceLogsToTimelineRows(
	entries: ServiceLog[],
): ServiceTimelineRow[] {
	return entries.map((entry) => ({
		sequence: `log:${entry.id}`,
		attempt_id: entry.attempt_id,
		level: entry.level,
		message: entry.message,
		timestamp: entry.timestamp,
		system: false,
	}));
}

/**
 * Merge the live tail into persisted rows (unit-tested).
 *
 * Streaming lines duplicate once the beat flush persists them, so rows
 * whose (timestamp, level, message) triple already persisted are
 * dropped. Streaming rows attribute to the live attempt (they are newer
 * than every persisted row, so the merged timeline stays chronological).
 */
export function mergeStreamingRows(
	persisted: ServiceTimelineRow[],
	streaming: StreamingLog[],
	liveAttemptId: string | null,
): ServiceTimelineRow[] {
	// Attempt-scoped: the same triple from two attempts is two lines.
	const keyOf = (attemptId: string | null, timestamp: string, level: string, message: string) =>
		`${attemptId}\n${timestamp}\n${level}\n${message}`;
	const seen = new Set(
		persisted.map((row) =>
			keyOf(row.attempt_id, row.timestamp, row.level, row.message),
		),
	);
	const fresh = streaming
		.filter(
			(entry) =>
				!seen.has(
					keyOf(liveAttemptId, entry.timestamp, entry.level, entry.message),
				),
		)
		.map((entry, index) => ({
			sequence: `stream:${index}`,
			attempt_id: liveAttemptId,
			level: entry.level,
			message: entry.message,
			timestamp: entry.timestamp,
			system: false,
		}));
	return [...persisted, ...fresh];
}
/**
 * Timeline filter (unit-tested). Text search and level stay live inside
 * ExecutionLogsPanel; this pass applies the attempt scope + date window.
 */
export function filterServiceLogs(
	logLines: ServiceTimelineRow[],
	filter: ServiceLogFilter,
): ServiceTimelineRow[] {
	const needle = filter.search.trim().toLowerCase();
	return logLines.filter((line) => {
		if (
			filter.attemptId !== "all" &&
			line.attempt_id !== filter.attemptId
		)
			return false;
		if (filter.level !== "all" && line.level !== filter.level)
			return false;
		if (needle && !line.message.toLowerCase().includes(needle))
			return false;
		const at = Date.parse(line.timestamp);
		if (filter.dateRange?.from && at < startOfDay(filter.dateRange.from).getTime())
			return false;
		if (filter.dateRange?.to && at > endOfDay(filter.dateRange.to).getTime())
			return false;
		return true;
	});
}

/** Last-exit reason with outcome severity (clean vs requested vs failure). */
export function ExitBadge({ reason }: { reason: string }) {
	const clean = reason === "clean_return";
	const requested =
		reason === "requested" ||
		reason === "stop_requested" ||
		reason.startsWith("stop");
	return (
		<Badge
			variant={clean || requested ? "outline" : "destructive"}
			className={
				clean
					? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
					: requested
						? "text-muted-foreground"
						: "bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
			}
			title="Last attempt exit reason"
		>
			{reason}
		</Badge>
	);
}

const ACTIONS: { action: ServiceAction; icon: typeof Play; label: string }[] = [
	{ action: "start", icon: Play, label: "Start" },
	{ action: "stop", icon: Square, label: "Stop" },
	{ action: "restart", icon: RotateCcw, label: "Restart" },
	{ action: "enable", icon: Power, label: "Enable" },
	{ action: "disable", icon: PowerOff, label: "Disable" },
];

export function ServiceDetailView({
	service,
	attempts,
	logEntries,
	logsTotal,
	hasOlderLogs,
	isLoadingOlderLogs,
	onLoadOlderLogs,
	isPlatformAdmin = false,
	onAction,
	onOpenInEditor,
	onBack,
	onRefresh,
	isRefreshing = false,
	dateRange,
	onDateRangeChange,
	streamingLogs,
	isStreamConnected,
}: ServiceDetailViewProps) {
	// Attempts live inside the timeline (start/ready/stop/exit are
	// first-class system rows) alongside persisted log output plus the
	// live tail: one continuous stream, oldest first. The panel applies
	// the date window itself, so it stays unset here (same predicate —
	// filterServiceLogs covers the full contract for server-side
	// filtering).
	const visibleLines = useMemo(() => {
		const merged = [
			...attemptsToTimelineRows(attempts),
			...serviceLogsToTimelineRows(logEntries),
		].sort(
			(a, b) =>
				Date.parse(a.timestamp) - Date.parse(b.timestamp) ||
				a.sequence.localeCompare(b.sequence),
		);
		const withLive = mergeStreamingRows(
			merged,
			streamingLogs,
			service.active_attempt_id ?? null,
		);
		return filterServiceLogs(withLive, {
			attemptId: "all",
			level: "all",
			search: "",
			dateRange: undefined,
		});
	}, [attempts, logEntries, streamingLogs, service.active_attempt_id]);

	const panelEntries: LogEntry[] = useMemo(
		() =>
			visibleLines.map((line, index) => ({
				id: index,
				timestamp: line.timestamp,
				level: line.level,
				message: `${line.system ? "[system] " : ""}${line.message}`,
				sequence: index,
			})),
		[visibleLines],
	);

	const actionDisabled = (action: ServiceAction): boolean => {
		if (action === "start") return service.desired_state === "running";
		if (action === "stop") return service.desired_state === "stopped";
		if (action === "restart" || action === "disable")
			return !service.enabled;
		return service.enabled;
	};

	return (
		<div className="flex h-full min-h-0 flex-col space-y-4">
			<div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1">
				{onBack && (
					<Button
						variant="ghost"
						size="sm"
						onClick={onBack}
						aria-label="Back to services"
					>
						<ArrowLeft className="mr-1 h-4 w-4" />
						Services
					</Button>
				)}
				<h2 className="font-mono text-lg font-semibold">
					{service.workflow_name}
				</h2>
				<Badge
					variant={
						service.observed_state === "running"
							? "default"
							: "outline"
					}
				>
					{formatObservedState(service.observed_state)}
				</Badge>
				<span aria-hidden="true" className="text-muted-foreground">
					·
				</span>
				<span className="text-sm text-muted-foreground">
					up{" "}
					{formatServiceUptime(service.active_attempt?.started_at)}
				</span>
				{!service.enabled && (
					<Badge variant="outline">disabled</Badge>
				)}
				{service.blocked_reason && (
					<Badge
						variant={
							service.blocked_reason === "crash_loop"
								? "destructive"
								: "outline"
						}
						className={
							service.blocked_reason === "policy"
								? "bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
								: ""
						}
					>
						Blocked:{" "}
						{service.blocked_reason === "policy"
							? "needs restart"
							: service.blocked_reason.replace("_", " ")}
					</Badge>
				)}
			</div>
			<p className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
				<Badge
					variant="outline"
					className={
						service.desired_state === "running"
							? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
							: "text-muted-foreground"
					}
				>
					Desired{" "}
					{service.desired_state === "running"
						? "Running"
						: "Stopped"}
				</Badge>
				{service.active_attempt?.worker_id && (
					<span className="inline-flex items-center gap-1.5">
						<Server
							aria-hidden="true"
							className="size-3.5 shrink-0"
						/>
						<span className="font-mono">
							{service.active_attempt.worker_id}
						</span>
					</span>
				)}
						<span>{service.restart_count} restarts</span>
						<span className="tabular-nums">
							{formatServiceMemory(service.memory_mb)}
						</span>
				<span>
					{RESTART_WORDS[service.restart_policy] ??
						service.restart_policy}
					{" · "}
					{STARTUP_WORDS[service.startup_policy] ??
						service.startup_policy}
				</span>
				{service.desired_state !== "running" &&
					service.last_exit_reason && (
						<span className="inline-flex items-center gap-1.5">
							Last exit:
							<ExitBadge reason={service.last_exit_reason} />
						</span>
					)}
			</p>

			<div className="flex shrink-0 flex-wrap gap-2">
				{ACTIONS.map(({ action, icon: Icon, label }) => (
					<Button
						key={action}
						variant="outline"
						disabled={actionDisabled(action)}
						onClick={() => onAction?.(service, action)}
					>
						<Icon aria-hidden="true" className="size-4" />
						{label}
					</Button>
				))}
				{onOpenInEditor && (
					<Button
						variant="outline"
						className="ml-auto"
						onClick={() => onOpenInEditor(service)}
					>
						<Code2 aria-hidden="true" className="size-4" />
						Open in editor
					</Button>
				)}
				{onRefresh && (
					<Button
						type="button"
						variant="outline"
						disabled={isRefreshing}
						onClick={onRefresh}
						aria-label="Refresh service"
					>
						<RefreshCw aria-hidden="true" className="size-4" />
						Refresh
					</Button>
				)}
			</div>

			<ExecutionLogsPanel
				logs={panelEntries}
				isPlatformAdmin={isPlatformAdmin}
				isConnected={isStreamConnected}
				variant="primary"
				maxHeight="min(60vh, 42rem)"
				showDateFilter
				dateRange={dateRange}
				onDateRangeChange={onDateRangeChange}
				sortOrder="desc"
				pagination={
					logsTotal > 0 ? (
						<div className="flex flex-wrap items-center gap-x-3 gap-2">
							<div className="flex items-center gap-1.5 text-sm text-muted-foreground">
								{isLoadingOlderLogs && (
									<Loader2
										className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
										aria-label="Loading older logs"
									/>
								)}
								<span aria-live="polite">
									Showing {logEntries.length} of{" "}
									{logsTotal} lines
								</span>
							</div>
							{hasOlderLogs && (
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									disabled={isLoadingOlderLogs}
									onClick={onLoadOlderLogs}
								>
									Load older logs
								</Button>
							)}
						</div>
					) : undefined
				}
			/>
		</div>
	);
}
