import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { ForkTable } from "./ForkTable";
import type { ExecutionRowData } from "./ExecutionRow";
import type { ProcessInfo, PoolDetail, PoolSummary, WorkerIncarnation } from "@/services/workers";
import { CONTAINER_COLORS } from "./MemoryChart";

type PoolData = PoolSummary | PoolDetail;

function formatUptime(seconds: number): string {
	if (seconds < 60) return `${Math.floor(seconds)}s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
	if (seconds < 86400) {
		const h = Math.floor(seconds / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		return m > 0 ? `${h}h ${m}m` : `${h}h`;
	}
	const d = Math.floor(seconds / 86400);
	const h = Math.floor((seconds % 86400) / 3600);
	return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function formatBytes(bytes: number): string {
	if (bytes < 0) return "N/A";
	const gb = bytes / (1024 * 1024 * 1024);
	if (gb >= 1) return `${gb.toFixed(1)} GB`;
	const mb = bytes / (1024 * 1024);
	return `${mb.toFixed(0)} MB`;
}

function getPoolCounts(pool: PoolData) {
	if ("processes" in pool && Array.isArray(pool.processes)) {
		const processes = pool.processes as ProcessInfo[];
		return {
			total: processes.length,
			idle: processes.filter((p) => p.state === "idle").length,
			busy: processes.filter((p) => p.state === "busy").length,
			processes,
		};
	}
	const summary = pool as PoolSummary;
	return {
		total: summary.pool_size ?? 0,
		idle: summary.idle_count ?? 0,
		busy: summary.busy_count ?? 0,
		processes: [] as ProcessInfo[],
	};
}

function getUptimeSeconds(pool: PoolData): number {
	const startedAt = pool.started_at;
	if (!startedAt) return 0;
	return (Date.now() - new Date(startedAt).getTime()) / 1000;
}

function formatLifecycleTime(value: string | null): string {
	return value ? new Date(value).toLocaleString() : "Not recorded";
}

function IncarnationDetails({ incarnation }: { incarnation: WorkerIncarnation }) {
	const cleanup = incarnation.cleanup_confirmed === null
		? null
		: incarnation.cleanup_confirmed
			? "Confirmed"
			: "Pending";
	return (
		<dl className="mt-4 grid grid-cols-1 gap-4 border-t pt-4 text-sm sm:grid-cols-3">
			<div>
				<dt className="text-xs text-muted-foreground">Worker</dt>
				<dd className="mt-1 flex flex-wrap items-center gap-2">
					<Badge variant={incarnation.state === "lost" ? "destructive" : "secondary"}>
						{incarnation.state}
					</Badge>
					<span>{incarnation.form} · {incarnation.execution_lane}</span>
				</dd>
			</div>
			<div className="min-w-0">
				<dt className="text-xs text-muted-foreground">Version & Scope</dt>
				<dd className="mt-1 space-y-1 [overflow-wrap:anywhere]">
					<div>{incarnation.version ?? "Version not reported"}</div>
					<div className="text-muted-foreground">{incarnation.capacity_scope ?? "Local worker"}</div>
				</dd>
			</div>
			<div>
				<dt className="text-xs text-muted-foreground">Lifecycle</dt>
				<dd className="mt-1 space-y-1 text-xs text-muted-foreground">
					<div>Created {formatLifecycleTime(incarnation.created_at)}</div>
					{incarnation.ready_at && <div>Ready {formatLifecycleTime(incarnation.ready_at)}</div>}
					{incarnation.ended_at && <div>Ended {formatLifecycleTime(incarnation.ended_at)}</div>}
					{incarnation.end_reason && <div>Reason: {incarnation.end_reason}</div>}
					{cleanup && <div>Cleanup: {cleanup}</div>}
				</dd>
			</div>
		</dl>
	);
}

interface ContainerTableProps {
	pools: PoolData[];
	/** Sorted worker IDs for consistent color assignment (same as chart) */
	workerIds: string[];
}

export function ContainerTable({ pools, workerIds }: ContainerTableProps) {
	return (
		<div className="space-y-3">
			{pools.length === 0 && (
				<p className="rounded-[var(--bf-radius-surface)] border p-[var(--bf-surface-pad)] text-sm text-muted-foreground">
					No worker containers are available.
				</p>
			)}
			{pools.map((pool) => (
				<ContainerRecord
					key={pool.worker_id}
					pool={pool}
					colorIndex={Math.max(0, workerIds.indexOf(pool.worker_id))}
				/>
			))}
		</div>
	);
}

function ContainerRecord({
	pool,
	colorIndex,
}: {
	pool: PoolData;
	colorIndex: number;
}) {
	const counts = getPoolCounts(pool);
	const memCurrent =
		"memory_current_bytes" in pool ? (pool.memory_current_bytes ?? -1) : -1;
	const memMax =
		"memory_max_bytes" in pool ? (pool.memory_max_bytes ?? -1) : -1;
	const executions = new Map<string, ExecutionRowData>();
	for (const process of counts.processes)
		if (process.state === "busy" && process.current_execution_id)
			executions.set(process.process_id, {
				execution_id: process.current_execution_id,
				workflow_name: process.current_execution_id,
				status: "RUNNING",
				elapsed_seconds: 0,
			});
	return (
		<article
			aria-label={`Container ${pool.worker_id}`}
			className="min-w-0 rounded-[var(--bf-radius-surface)] border bg-card p-[var(--bf-surface-pad)]"
		>
			<div className="flex flex-wrap items-start justify-between gap-3">
				<h3 className="flex min-w-0 items-start gap-2 font-mono text-sm font-medium [overflow-wrap:anywhere]">
					<span
						aria-hidden="true"
						className="mt-1 size-2 shrink-0 rounded-sm"
						style={{
							backgroundColor:
								CONTAINER_COLORS[
									colorIndex % CONTAINER_COLORS.length
								],
						}}
					/>
					<span className="min-w-0">{pool.worker_id}</span>
				</h3>
				<Badge
					variant={
						pool.status === "online" ? "secondary" : "destructive"
					}
				>
					{pool.status ?? "offline"}
				</Badge>
			</div>
			<dl className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
				<div>
					<dt className="text-xs text-muted-foreground">Processes</dt>
					<dd className="mt-1 tabular-nums">
						{counts.total} total · {counts.busy} busy ·{" "}
						{counts.idle} idle
					</dd>
				</div>
				<div className="min-w-0">
					<dt className="text-xs text-muted-foreground">Memory</dt>
					<dd className="mt-1 space-y-2 tabular-nums">
						{memCurrent < 0 ? (
							"Unavailable"
						) : (
							<>
								<span>
									{formatBytes(memCurrent)}
									{memMax > 0
										? ` / ${formatBytes(memMax)}`
										: " · no limit"}
								</span>
								{memMax > 0 && (
									<Progress
										aria-label={`${pool.worker_id} memory usage`}
										value={(memCurrent / memMax) * 100}
										className="h-1.5"
									/>
								)}
							</>
						)}
					</dd>
				</div>
				<div>
					<dt className="text-xs text-muted-foreground">Uptime</dt>
					<dd className="mt-1 tabular-nums">
						{pool.started_at
							? formatUptime(getUptimeSeconds(pool))
							: "Unavailable"}
					</dd>
				</div>
			</dl>
			{pool.incarnation && <IncarnationDetails incarnation={pool.incarnation} />}
			<details className="group mt-4 border-t pt-2">
				<summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--bf-radius-control)] text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
					View processes{" "}
					<ChevronDown
						aria-hidden="true"
						className="size-4 shrink-0 transition-transform duration-[var(--bf-motion-disclosure)] group-open:rotate-180 motion-reduce:transition-none"
					/>
				</summary>
				{counts.processes.length > 0 ? (
					<div className="min-w-0 pt-3">
						<ForkTable
							workerId={pool.worker_id}
							processes={counts.processes}
							executions={executions}
							containerMemoryMax={memMax > 0 ? memMax : undefined}
							actionsEnabled={pool.incarnation?.state === undefined || pool.incarnation.state === "ready"}
						/>
					</div>
				) : (
					<p className="py-3 text-sm text-muted-foreground">
						Process details are not available for this container.
					</p>
				)}
			</details>
		</article>
	);
}
