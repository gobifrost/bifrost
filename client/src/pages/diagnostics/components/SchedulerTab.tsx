import { SchedulerReplicaList } from "./SchedulerReplicaList";
import { SchedulerTaskList } from "./SchedulerTaskList";
import { useState } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Activity,
	AlertTriangle,
	Clock3,
	Cpu,
	HardDrive,
	Loader2,
	RefreshCw,
	Server,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	getSchedulerDiagnostics,
	type SchedulerDiagnosticsResponse,
	type SchedulerTaskStatus,
} from "@/services/schedulerDiagnostics";
import { PlatformJobsPanel } from "./PlatformJobsPanel";
import { SchedulerRunDrawer } from "./SchedulerRunDrawer";

// Work normally starts within a second; a minute means capacity is short.
const RUN_WAIT_WARNING_SECONDS = 60;

export function schedulerRecommendations(
	data: SchedulerDiagnosticsResponse,
): string[] {
	const { capacity } = data;
	const recommendations: string[] = [];
	if (
		capacity.oldest_waiting_run_seconds != null &&
		capacity.oldest_waiting_run_seconds >= RUN_WAIT_WARNING_SECONDS
	) {
		recommendations.push(
			`${capacity.runs_waiting} run${capacity.runs_waiting === 1 ? " is" : "s are"} waiting for an executor with room; the oldest for ${Math.round(capacity.oldest_waiting_run_seconds)}s. Add executors or executor memory.`,
		);
	}
	if (capacity.jobs_waiting_for_memory > 0) {
		recommendations.push(
			`${capacity.jobs_waiting_for_memory} queued job${capacity.jobs_waiting_for_memory === 1 ? " is" : "s are"} waiting for memory admission. Compare available and required headroom in Platform Jobs below.`,
		);
	} else if (
		capacity.max_memory_utilization_percent != null &&
		capacity.max_memory_utilization_percent >= 85
	) {
		recommendations.push(
			`Scheduler memory reached ${capacity.max_memory_utilization_percent.toFixed(0)}%. Add memory before admitting larger jobs.`,
		);
	}
	return recommendations;
}

function StatCard({
	title,
	value,
	detail,
	icon: Icon,
}: {
	title: string;
	value: string;
	detail: string;
	icon: typeof Activity;
}) {
	return (
		<Card>
			<CardContent className="pt-5">
				<div className="flex items-start justify-between gap-3">
					<div>
						<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
							{title}
						</p>
						<p className="mt-1 text-2xl font-semibold">{value}</p>
						<p className="mt-1 text-xs text-muted-foreground">
							{detail}
						</p>
					</div>
					<Icon className="h-5 w-5 text-muted-foreground" />
				</div>
			</CardContent>
		</Card>
	);
}

export function SchedulerTab() {
	const queryClient = useQueryClient();
	const [selectedTask, setSelectedTask] =
		useState<SchedulerTaskStatus | null>(null);
	const query = useQuery({
		queryKey: ["scheduler-diagnostics"],
		queryFn: ({ signal }) => getSchedulerDiagnostics({ signal }),
		refetchInterval: 10_000,
	});
	const platformJobsFetching = useIsFetching({ queryKey: ["platform-jobs"] });
	const isRefreshing = query.isFetching || platformJobsFetching > 0;
	const data = query.data;

	if (query.isLoading && !data) {
		return (
			<div className="flex justify-center py-16">
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}
	if (!data) {
		return (
			<Alert variant="destructive">
				<AlertTitle>Scheduler diagnostics unavailable</AlertTitle>
				<AlertDescription>
					Scheduler data could not be loaded.
				</AlertDescription>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 mt-3 w-fit"
					disabled={query.isFetching}
					onClick={() => {
						void query.refetch();
					}}
				>
					{query.isFetching ? "Retrying…" : "Retry scheduler"}
				</Button>
			</Alert>
		);
	}

	const recommendations = schedulerRecommendations(data);
	const memory = data.capacity.max_memory_utilization_percent;
	const availableMemory = data.replicas.reduce<number | null>(
		(largest, replica) => {
			if (
				!replica.online ||
				replica.memory_current_bytes == null ||
				replica.memory_limit_bytes == null
			)
				return largest;
			const headroom = Math.max(
				0,
				replica.memory_limit_bytes - replica.memory_current_bytes,
			);
			return largest == null ? headroom : Math.max(largest, headroom);
		},
		null,
	);

	return (
		<>
			<div
				className="max-w-[1100px] mx-auto space-y-6"
				data-testid="scheduler-diagnostics"
			>
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div>
						<div className="flex flex-wrap items-center gap-2">
							<h2 className="text-lg font-semibold">Scheduler</h2>
							<Badge
								variant={
									data.leader.healthy
										? "outline"
										: "destructive"
								}
								className={
									data.leader.healthy
										? "border-[var(--bf-success)]/30 bg-[var(--bf-success)]/10 text-[var(--bf-success)]"
										: undefined
								}
							>
								{data.leader.healthy
									? "Leader healthy"
									: "No leader"}
							</Badge>
						</div>
						<p className="mt-1 text-sm text-muted-foreground">
							Durable system jobs, trigger health, and scheduler
							capacity.
						</p>
					</div>
					<Tooltip>
						<TooltipTrigger asChild>
							<Button
								variant="outline"
								size="icon"
								type="button"
								className="min-h-11 min-w-11 shrink-0"
								aria-label="Refresh scheduler diagnostics"
								onClick={() => {
									void query.refetch();
									void queryClient.invalidateQueries({
										queryKey: ["platform-jobs"],
									});
								}}
								disabled={isRefreshing}
							>
								<RefreshCw
									className={`h-4 w-4 ${isRefreshing ? "animate-spin motion-reduce:animate-none" : ""}`}
								/>
							</Button>
						</TooltipTrigger>
						<TooltipContent>Refresh</TooltipContent>
					</Tooltip>
				</div>

				{query.error && (
					<Alert variant="destructive">
						<AlertTitle>Scheduler refresh failed</AlertTitle>
						<AlertDescription>
							Showing the last available snapshot. Use Refresh to
							try again.
						</AlertDescription>
					</Alert>
				)}
				{recommendations.length > 0 ? (
					<Alert className="border-[var(--bf-warning)]/40">
						<AlertTriangle className="h-4 w-4 text-[var(--bf-warning)]" />
						<AlertTitle>Capacity action recommended</AlertTitle>
						<AlertDescription>
							<ul className="mt-1 list-disc space-y-1 pl-4">
								{recommendations.map((item) => (
									<li key={item}>{item}</li>
								))}
							</ul>
						</AlertDescription>
					</Alert>
				) : (
					<Alert className="border-[var(--bf-success)]/30">
						<Activity className="h-4 w-4 text-[var(--bf-success)]" />
						<AlertTitle>Capacity looks healthy</AlertTitle>
						<AlertDescription>
							No sustained queue or memory pressure is visible in
							this snapshot.
						</AlertDescription>
					</Alert>
				)}

				<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
					<StatCard
						title="Runs Waiting"
						value={`${data.capacity.runs_waiting}`}
						detail={
							data.capacity.oldest_waiting_run_seconds == null
								? "No runs waiting"
								: `Oldest waiting ${Math.round(data.capacity.oldest_waiting_run_seconds)}s`
						}
						icon={Clock3}
					/>
					<StatCard
						title="Replicas"
						value={`${data.capacity.replicas_online}`}
						detail={`${data.capacity.platform_jobs_running} platform job${data.capacity.platform_jobs_running === 1 ? "" : "s"} running`}
						icon={Server}
					/>
					<StatCard
						title="Queue"
						value={`${data.capacity.jobs_queued}`}
						detail={
							data.capacity.oldest_queued_seconds == null
								? "No jobs waiting"
								: `Oldest waiting ${Math.round(data.capacity.oldest_queued_seconds)}s`
						}
						icon={Clock3}
					/>
					<StatCard
						title="Memory Waits"
						value={`${data.capacity.jobs_waiting_for_memory}`}
						detail="See required headroom below"
						icon={HardDrive}
					/>
					<StatCard
						title="Highest Replica Use"
						value={
							memory == null
								? "Unbounded"
								: `${memory.toFixed(0)}%`
						}
						detail="Current scheduler snapshot"
						icon={Cpu}
					/>
				</div>

				<section
					aria-labelledby="scheduler-replicas-heading"
					className="space-y-3"
				>
					<div>
						<h3
							id="scheduler-replicas-heading"
							className="font-semibold"
						>
							Scheduler Replicas
						</h3>
						<p className="mt-1 text-sm text-muted-foreground">
							Live capacity and workload across scheduler
							instances.
						</p>
					</div>
					<SchedulerReplicaList replicas={data.replicas} />
					{data.replicas.length === 0 && (
						<p className="rounded-[var(--bf-radius-surface)] border border-dashed py-8 text-center text-sm text-muted-foreground">
							No scheduler replicas have reported a heartbeat.
						</p>
					)}
				</section>

				<Tabs defaultValue="platform-jobs" className="space-y-3">
					<TabsList
						aria-label="Scheduler views"
						className="h-auto w-full flex-wrap sm:w-fit"
					>
						<TabsTrigger className="min-h-11" value="platform-jobs">
							Platform Jobs
						</TabsTrigger>
						<TabsTrigger
							className="min-h-11"
							value="system-schedules"
						>
							System Schedules
						</TabsTrigger>
					</TabsList>
					<TabsContent value="platform-jobs" className="mt-0">
						<PlatformJobsPanel
							availableMemoryBytes={availableMemory}
						/>
					</TabsContent>
					<TabsContent value="system-schedules" className="mt-0">
						<section
							aria-labelledby="system-schedules-heading"
							className="space-y-3"
						>
							<div>
								<h3
									id="system-schedules-heading"
									className="font-semibold"
								>
									System Schedules
								</h3>
								<p className="mt-1 text-sm text-muted-foreground">
									Registered recurring triggers and their
									latest run.
								</p>
							</div>
							<SchedulerTaskList
								tasks={data.tasks}
								onSelect={setSelectedTask}
							/>
							{data.tasks.length === 0 && (
								<p className="rounded-[var(--bf-radius-surface)] border border-dashed py-8 text-center text-sm text-muted-foreground">
									No System Schedules are registered.
								</p>
							)}
						</section>
					</TabsContent>
				</Tabs>
			</div>
			<SchedulerRunDrawer
				task={selectedTask}
				onClose={() => setSelectedTask(null)}
			/>
		</>
	);
}
