import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { AlertCircle } from "lucide-react";
import {
	Card,
	CardAction,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	ChartContainer,
	ChartTooltip,
	ChartTooltipContent,
	type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	bucketExecutions,
	clampBucketsToData,
	type BucketableExecution,
	type ChartWindow,
	type OutcomeSummary,
} from "@/lib/execution-buckets";

interface ExecutionsOverTimeCardProps {
	window: ChartWindow;
	onWindowChange: (window: ChartWindow) => void;
	executions: readonly BucketableExecution[] | undefined;
	/** Shared outcome tally — the same object the stat cards render. */
	outcomes: OutcomeSummary;
	/** True when the window fetch hit the API row cap (more rows exist). */
	truncated: boolean;
	isLoading: boolean;
	isError: boolean;
}

const chartConfig = {
	success: {
		label: "Success",
		color: "var(--chart-2)",
	},
	failed: {
		label: "Failed",
		color: "var(--destructive)",
	},
} satisfies ChartConfig;

export const WINDOW_LABELS: Record<ChartWindow, string> = {
	"24h": "Last 24 hours",
	"7d": "Last 7 days",
	"30d": "Last 30 days",
};

export function ExecutionsOverTimeCard({
	window,
	onWindowChange,
	executions,
	outcomes,
	truncated,
	isLoading,
	isError,
}: ExecutionsOverTimeCardProps) {
	// Truncated fetches only cover [oldest fetched row, now]; clamping the
	// bucket range keeps older buckets from rendering as a fake-zero cliff.
	const buckets = useMemo(() => {
		const full = bucketExecutions(executions ?? [], window);
		return truncated ? clampBucketsToData(full, executions ?? []) : full;
	}, [executions, window, truncated]);

	return (
		<Card>
			<CardHeader>
				<CardTitle>Executions</CardTitle>
				<CardDescription>
					{isLoading ? (
						WINDOW_LABELS[window]
					) : (
						<>
							{`${WINDOW_LABELS[window]} · ${outcomes.total.toLocaleString()} ${
								outcomes.total === 1 ? "run" : "runs"
							}`}
							{outcomes.failed > 0 && (
								<>
									{" · "}
									<Link
										to="/history?status=Failed"
										className="text-destructive transition-colors hover:underline"
									>
										{outcomes.failed.toLocaleString()} failed
									</Link>
								</>
							)}
							{truncated && (
								<span data-testid="executions-chart-truncated">
									{" · showing latest 1,000 runs"}
								</span>
							)}
						</>
					)}
				</CardDescription>
				<CardAction>
					<ToggleGroup
						type="single"
						value={window}
						onValueChange={(value) => {
							if (value) onWindowChange(value as ChartWindow);
						}}
						className="rounded-md bg-muted/50 p-0.5 ring-1 ring-foreground/5"
					>
						<ToggleGroupItem
							value="24h"
							aria-label="Last 24 hours"
							className="h-6 rounded-[5px] px-2 text-xs"
						>
							24h
						</ToggleGroupItem>
						<ToggleGroupItem
							value="7d"
							aria-label="Last 7 days"
							className="h-6 rounded-[5px] px-2 text-xs"
						>
							7d
						</ToggleGroupItem>
						<ToggleGroupItem
							value="30d"
							aria-label="Last 30 days"
							className="h-6 rounded-[5px] px-2 text-xs"
						>
							30d
						</ToggleGroupItem>
					</ToggleGroup>
				</CardAction>
			</CardHeader>
			<CardContent>
				{isLoading ? (
					<Skeleton className="h-[220px] w-full" />
				) : isError ? (
					<div
						className="flex h-[220px] flex-col items-center justify-center gap-2 text-center"
						data-testid="executions-chart-error"
					>
						<AlertCircle className="h-6 w-6 text-destructive" />
						<p className="text-sm text-muted-foreground">
							Couldn't load executions for this window.
						</p>
					</div>
				) : outcomes.total === 0 ? (
					<div
						className="flex h-[220px] flex-col items-center justify-center gap-1 text-center"
						data-testid="executions-chart-empty"
					>
						<p className="text-sm font-medium">
							No executions in this window
						</p>
						<p className="text-sm text-muted-foreground">
							Runs will chart here as workflows execute.
						</p>
					</div>
				) : (
					<ChartContainer
						config={chartConfig}
						className="aspect-auto h-[220px] w-full"
					>
						<AreaChart
							data={buckets}
							margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
						>
							<defs>
								<linearGradient
									id="fillSuccess"
									x1="0"
									y1="0"
									x2="0"
									y2="1"
								>
									<stop
										offset="5%"
										stopColor="var(--color-success)"
										stopOpacity={0.5}
									/>
									<stop
										offset="95%"
										stopColor="var(--color-success)"
										stopOpacity={0.05}
									/>
								</linearGradient>
								<linearGradient
									id="fillFailed"
									x1="0"
									y1="0"
									x2="0"
									y2="1"
								>
									<stop
										offset="5%"
										stopColor="var(--color-failed)"
										stopOpacity={0.5}
									/>
									<stop
										offset="95%"
										stopColor="var(--color-failed)"
										stopOpacity={0.05}
									/>
								</linearGradient>
							</defs>
							<CartesianGrid vertical={false} />
							<XAxis
								dataKey="label"
								tickLine={false}
								axisLine={false}
								tickMargin={8}
								minTickGap={24}
							/>
							<YAxis
								allowDecimals={false}
								width={32}
								tickLine={false}
								axisLine={false}
							/>
							<ChartTooltip
								cursor={false}
								content={<ChartTooltipContent indicator="line" />}
							/>
							<Area
								dataKey="success"
								type="monotone"
								stroke="var(--color-success)"
								strokeWidth={2}
								fill="url(#fillSuccess)"
							/>
							<Area
								dataKey="failed"
								type="monotone"
								stroke="var(--color-failed)"
								strokeWidth={2}
								fill="url(#fillFailed)"
							/>
						</AreaChart>
					</ChartContainer>
				)}
			</CardContent>
		</Card>
	);
}
