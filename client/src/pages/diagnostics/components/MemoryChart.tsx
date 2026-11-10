// client/src/pages/diagnostics/components/MemoryChart.tsx
import { useMemo, useState } from "react";
import { useReducedMotion } from "framer-motion";
import {
	AreaChart,
	Area,
	XAxis,
	YAxis,
	CartesianGrid,
	Tooltip,
	ResponsiveContainer,
	ReferenceLine,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	useWorkerMetrics,
	type WorkerMetricPoint,
	type PoolSummary,
	type PoolDetail,
	type ProcessInfo,
} from "@/services/workers";

const TIME_RANGES = ["1h", "6h", "24h", "7d"] as const;
type TimeRange = (typeof TIME_RANGES)[number];

// Consistent colors for up to 10 containers — keep alarm-y warm tones at the end
// so a single-container view doesn't look like an error.
export const CONTAINER_COLORS = [
	"#3b82f6", // blue-500
	"#10b981", // emerald-500
	"#06b6d4", // cyan-500
	"#8b5cf6", // violet-500
	"#14b8a6", // teal-500
	"#ec4899", // pink-500
	"#f59e0b", // amber-500
	"#84cc16", // lime-500
	"#6366f1", // indigo-500
	"#f97316", // orange-500
];

type LivePool = PoolSummary | PoolDetail;

interface ChartDataPoint {
	group: string;
	total: number | null;
}

function formatBytes(bytes: number): string {
	if (bytes < 0) return "N/A";
	const gb = bytes / (1024 * 1024 * 1024);
	if (gb >= 1) return `${gb.toFixed(1)} GB`;
	const mb = bytes / (1024 * 1024);
	return `${mb.toFixed(0)} MB`;
}

interface MemoryChartProps {
	/** Optional live data points from WebSocket to append */
	livePoints?: WorkerMetricPoint[];
	/**
	 * Live pool data from WebSocket. When present, the header denominator
	 * (total memory limit + container count) is sourced from this instead of
	 * the 60s-polled metrics endpoint, so it stays in sync with ContainerTable.
	 */
	livePools?: LivePool[];
}

export function MemoryChart({ livePoints, livePools }: MemoryChartProps) {
	const reducedMotion = useReducedMotion();
	const [range, setRange] = useState<TimeRange>("1h");
	const { data, isLoading, isError, isFetching, refetch } =
		useWorkerMetrics(range);

	const {
		chartData,
		workerIds,
		totalCurrent,
		totalMax,
		hasUnlimitedWorker,
		hasUnknownMemory,
	} = useMemo(() => {
		const allPoints = [...(data?.points ?? []), ...(livePoints ?? [])];
		const hasLivePools = !!livePools && livePools.length > 0;
		if (allPoints.length === 0 && !hasLivePools) {
			return {
				chartData: [],
				workerIds: [],
				totalCurrent: 0,
				totalMax: 0,
				hasUnlimitedWorker: false,
				hasUnknownMemory: false,
			};
		}

		// Labels repeat across days. Group by the exact instant when supplied.
		const buckets = new Map<
			string,
			{ group: string; workers: Map<string, number> }
		>();
		for (const point of allPoints) {
			const key = point.bucket_start ?? point.group;
			let bucket = buckets.get(key);
			if (!bucket) {
				bucket = { group: point.group, workers: new Map() };
				buckets.set(key, bucket);
			}
			bucket.workers.set(point.worker_id, point.memory_current);
		}
		const result: ChartDataPoint[] = [...buckets.values()].map((bucket) => {
			const samples = [...bucket.workers.values()];
			return {
				group: bucket.group,
				total: samples.some((sample) => sample < 0)
					? null
					: samples.reduce((sum, sample) => sum + sample, 0),
			};
		});

		// Compute current totals from latest data points (server returns in order)
		const latestByWorker = new Map<string, WorkerMetricPoint>();
		for (const point of allPoints) {
			latestByWorker.set(point.worker_id, point);
		}
		let current = 0;
		let max = 0;
		let unlimited = false;
		let unknown = false;
		for (const point of latestByWorker.values()) {
			if (point.memory_current < 0) unknown = true;
			else current += point.memory_current;
			if (point.memory_max > 0) {
				max += point.memory_max;
			} else {
				unlimited = true;
			}
		}

		// If we have live pool data from the WebSocket, prefer it for the
		// header totals — the metrics endpoint only refreshes every 60s, so
		// its denominator goes stale as containers come and go.
		let headerWorkerIds: string[] = [
			...new Set(allPoints.map((p) => p.worker_id)),
		];
		if (hasLivePools) {
			let liveCurrent = 0;
			let liveMax = 0;
			let liveUnlimited = false;
			let liveUnknown = false;
			const liveIds: string[] = [];
			for (const pool of livePools!) {
				liveIds.push(pool.worker_id);
				const memCurrent =
					"memory_current_bytes" in pool &&
					pool.memory_current_bytes != null
						? pool.memory_current_bytes
						: -1;
				const memMax =
					"memory_max_bytes" in pool && pool.memory_max_bytes != null
						? pool.memory_max_bytes
						: 0;
				if (memCurrent < 0) liveUnknown = true;
				else liveCurrent += memCurrent;
				if (memMax > 0) {
					liveMax += memMax;
				} else {
					// Only treat as unlimited if we know there are processes
					// running — an empty pool with memMax=0 just means we
					// haven't seen a heartbeat yet.
					if (
						"processes" in pool &&
						Array.isArray(pool.processes) &&
						(pool.processes as ProcessInfo[]).length > 0
					) {
						liveUnlimited = true;
					}
				}
			}
			current = liveCurrent;
			max = liveMax;
			unlimited = liveUnlimited;
			unknown = liveUnknown;
			headerWorkerIds = liveIds.sort();
		}

		return {
			chartData: result,
			workerIds: headerWorkerIds,
			totalCurrent: current,
			totalMax: max,
			hasUnlimitedWorker: unlimited,
			hasUnknownMemory: unknown,
		};
	}, [data, livePoints, livePools]);

	const hasData = chartData.length > 0 || workerIds.length > 0;
	const showLimit = totalMax > 0 && !hasUnlimitedWorker && !hasUnknownMemory;
	const thresholdBytes = totalMax * 0.85;
	const utilizationPct = showLimit
		? ((totalCurrent / totalMax) * 100).toFixed(0)
		: "0";

	if (isLoading) {
		return (
			<Card>
				<CardContent className="pt-6">
					<Skeleton className="h-[250px] w-full" />
				</CardContent>
			</Card>
		);
	}

	return (
		<Card>
			<CardContent className="pt-6">
				{isError && (
					<Alert variant="destructive" className="mb-4">
						<AlertTitle>
							Memory metrics could not be{" "}
							{data ? "refreshed" : "loaded"}
						</AlertTitle>
						<AlertDescription>
							{data
								? "The last available history is still shown."
								: "Retry to load memory history. Live container totals remain available."}
						</AlertDescription>
						<Button
							type="button"
							variant="outline"
							className="mt-3 min-h-11"
							disabled={isFetching}
							onClick={() => void refetch()}
						>
							{isFetching ? "Retrying…" : "Retry memory metrics"}
						</Button>
					</Alert>
				)}
				{/* Header */}
				<div className="mb-4 flex min-w-0 flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
					<div>
						<div className="text-xs text-muted-foreground uppercase tracking-wider">
							Total Memory Usage
						</div>
						<div className="mt-1 flex flex-wrap items-baseline gap-2">
							{!hasData ? (
								<span className="text-sm text-muted-foreground">
									{isError
										? "Memory history unavailable"
										: "No metrics data yet"}
								</span>
							) : hasUnknownMemory ? (
								<span className="text-sm text-muted-foreground">
									Memory usage is unavailable for one or more
									containers.
								</span>
							) : showLimit ? (
								<>
									<span className="text-3xl font-bold">
										{formatBytes(totalCurrent)}
									</span>
									<span className="text-sm text-muted-foreground">
										/ {formatBytes(totalMax)} across{" "}
										{workerIds.length} container
										{workerIds.length !== 1 ? "s" : ""}
									</span>
								</>
							) : (
								<>
									<span className="text-3xl font-bold">
										{formatBytes(totalCurrent)}
									</span>
									<span className="text-sm text-muted-foreground">
										across {workerIds.length} container
										{workerIds.length !== 1 ? "s" : ""}{" "}
										&middot; no memory limit set
									</span>
								</>
							)}
						</div>
						{showLimit && (
							<div className="text-xs text-muted-foreground mt-0.5">
								{utilizationPct}% utilized &middot; Threshold:
								85%
							</div>
						)}
					</div>
					<div className="flex gap-1">
						{TIME_RANGES.map((r) => (
							<Button
								key={r}
								variant={range === r ? "default" : "ghost"}
								size="sm"
								className="min-h-11 px-3 text-xs"
								type="button"
								aria-pressed={range === r}
								onClick={() => setRange(r)}
							>
								{r}
							</Button>
						))}
					</div>
				</div>

				{/* Chart */}
				{chartData.length === 0 ? (
					<div className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
						{isError
							? "Memory history unavailable"
							: "No metrics data available yet"}
					</div>
				) : (
					<ResponsiveContainer width="100%" height={200}>
						<AreaChart data={chartData}>
							<CartesianGrid
								strokeDasharray="3 3"
								className="stroke-muted"
							/>
							<XAxis
								dataKey="group"
								tick={{
									fontSize: 11,
									fill: "var(--muted-foreground)",
								}}
								tickLine={false}
								axisLine={false}
							/>
							<YAxis
								tick={{
									fontSize: 11,
									fill: "var(--muted-foreground)",
								}}
								tickLine={false}
								axisLine={false}
								tickFormatter={(v) => formatBytes(v)}
								width={60}
								domain={showLimit ? [0, totalMax] : [0, "auto"]}
								allowDataOverflow={false}
							/>
							<Tooltip
								contentStyle={{
									backgroundColor: "var(--card)",
									border: "1px solid var(--border)",
									borderRadius: "var(--bf-radius-control)",
									color: "var(--foreground)",
									fontSize: "12px",
								}}
								formatter={(value, name) => [
									formatBytes(Number(value ?? 0)),
									name as string,
								]}
								labelFormatter={(label) => label}
							/>
							{showLimit && (
								<ReferenceLine
									y={thresholdBytes}
									stroke="var(--destructive)"
									strokeDasharray="4 4"
									strokeOpacity={0.5}
									label={{
										value: "85%",
										position: "right",
										style: {
											fontSize: 10,
											fill: "var(--destructive)",
										},
									}}
								/>
							)}
							<Area
								isAnimationActive={!reducedMotion}
								animationDuration={220}
								type="monotone"
								dataKey="total"
								name="Total memory"
								fill="var(--primary)"
								fillOpacity={0.25}
								stroke="var(--primary)"
								strokeWidth={1.75}
								activeDot={{ r: 4 }}
							/>
						</AreaChart>
					</ResponsiveContainer>
				)}
			</CardContent>
		</Card>
	);
}
