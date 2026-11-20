import { useState, useMemo } from "react";
import { RefreshCw, Loader2, WifiOff, Server } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	usePools,
	useQueueStatus,
	type PoolSummary,
} from "@/services/workers";
import { getErrorMessage } from "@/lib/api-error";
import { QueueBadge } from "./QueueBadge";
import { MemoryChart } from "./MemoryChart";
import { ContainerTable } from "./ContainerTable";
import { useWorkerWebSocket } from "../hooks/useWorkerWebSocket";

export function WorkersTab() {
	const {
		data: poolsData,
		isLoading: poolsLoading,
		error: poolsError,
		refetch: refetchPools,
	} = usePools({ includeIncarnations: true });

	const {
		data: queueData,
		isLoading: queueLoading,
		error: queueError,
		isFetching: queueFetching,
		refetch: refetchQueue,
	} = useQueueStatus({ limit: 50 });

	const { pools: wsPools, isConnected } = useWorkerWebSocket();

	const [isRefreshing, setIsRefreshing] = useState(false);

	const handleRefresh = async () => {
		setIsRefreshing(true);
		try {
			await Promise.all([refetchPools(), refetchQueue()]);
		} finally {
			setIsRefreshing(false);
		}
	};

	// Merge REST snapshot + WS heartbeats by worker_id. The REST call returns
	// the full container list on mount (fast — Redis SCAN); WS heartbeats then
	// upgrade individual rows with live process/memory data as they arrive.
	// Using either/or here caused containers to "trickle in" because the first
	// WS heartbeat would replace the full REST list with a single-container WS list.
	const pools = useMemo(() => {
		const byId = new Map<string, PoolSummary | (typeof wsPools)[number]>();
		for (const p of poolsData?.pools ?? []) byId.set(p.worker_id, p);
		for (const p of wsPools) {
			const canonical = byId.get(p.worker_id);
			byId.set(
				p.worker_id,
				canonical && "incarnation" in canonical
					? { ...canonical, ...p, incarnation: canonical.incarnation }
					: p,
			);
		}
		return [...byId.values()];
	}, [poolsData, wsPools]);
	const queueItems = queueData?.items || [];

	// Stable sorted worker IDs for consistent color assignment
	const workerIds = useMemo(
		() => [...new Set(pools.map((p) => p.worker_id))].sort(),
		[pools],
	);

	// Compute summary stats
	const stats = useMemo(() => {
		let totalForks = 0;
		let totalBusy = 0;
		for (const pool of pools) {
			if ("processes" in pool && Array.isArray(pool.processes)) {
				totalForks += pool.processes.length;
				totalBusy += pool.processes.filter((p) => p.state === "busy").length;
			} else {
				const summary = pool as PoolSummary;
				totalForks += summary.pool_size ?? 0;
				totalBusy += summary.busy_count ?? 0;
			}
		}
		return { containers: pools.length, forks: totalForks, busy: totalBusy };
	}, [pools]);

	return (
		<div className="max-w-[1100px] mx-auto space-y-6">
			{/* Connection Status Banner */}
			{!isConnected && (
				<Alert className="border-[var(--bf-warning)]/30">
					<WifiOff className="h-4 w-4" />
					<AlertDescription>
						Connecting to real-time worker updates... Data may not
						be current.
					</AlertDescription>
				</Alert>
			)}

			{/* Error State */}
			{poolsError && (
				<Alert variant="destructive">
					<AlertDescription>
						Failed to load pools:{" "}
						{getErrorMessage(poolsError, "Unknown error")}
					</AlertDescription>
				</Alert>
			)}

			{/* Header */}
			<div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
				<div className="flex min-w-0 flex-wrap items-center gap-3">
					<h2 className="text-lg font-semibold">Process Pools</h2>
					{isConnected && (
						<span className="flex items-center gap-1 text-xs text-[var(--bf-success)]">
							<span className="w-2 h-2 bg-[var(--bf-success)] rounded-full animate-pulse motion-reduce:animate-none" />
							Live
						</span>
					)}
					<QueueBadge
						items={queueItems}
						total={queueData?.total}
						isLoading={queueLoading}
						error={
							queueError
								? getErrorMessage(
										queueError,
										"Could not load the execution queue.",
									)
								: undefined
						}
						onRetry={() => {
							void refetchQueue();
						}}
						isRetrying={queueFetching}
					/>
				</div>
				<div className="flex min-w-0 flex-wrap items-center gap-3">
					<span className="text-sm text-muted-foreground">
						{stats.containers} container
						{stats.containers !== 1 ? "s" : ""} &middot;{" "}
						{stats.forks} fork{stats.forks !== 1 ? "s" : ""}
					</span>
					<Button
						type="button"
						className="min-h-11"
						variant="outline"
						size="sm"
						onClick={handleRefresh}
						disabled={isRefreshing || poolsLoading}
					>
						<RefreshCw
							className={`h-4 w-4 mr-2 ${isRefreshing ? "animate-spin motion-reduce:animate-none" : ""}`}
						/>
						Refresh
					</Button>
				</div>
			</div>

			{/* Memory Chart */}
			<MemoryChart livePools={pools} />

			{/* Container Table */}
			{poolsLoading && pools.length === 0 ? (
				<div className="flex items-center justify-center py-12">
					<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
				</div>
			) : poolsError && pools.length === 0 ? null : pools.length === 0 ? (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12 text-center">
						<Server className="h-12 w-12 text-muted-foreground mb-4" />
						<h3 className="text-lg font-semibold">
							No containers connected
						</h3>
						<p className="mt-2 text-sm text-muted-foreground max-w-md">
							Worker containers register themselves on startup. If
							you expect containers to be running, check the
							worker logs for connection issues.
						</p>
					</CardContent>
				</Card>
			) : (
				<ContainerTable pools={pools} workerIds={workerIds} />
			)}
		</div>
	);
}
