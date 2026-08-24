import { useState } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
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
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
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

function formatBytes(value: number | null | undefined) {
	if (value == null) return "Not limited";
	const units = ["B", "KiB", "MiB", "GiB", "TiB"];
	let amount = value;
	let unit = 0;
	while (amount >= 1024 && unit < units.length - 1) {
		amount /= 1024;
		unit += 1;
	}
	return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}
function relativeTime(value: string | null | undefined) {
	if (!value) return "Never";
	return formatDistanceToNow(new Date(value), { addSuffix: true });
}

function statusVariant(status: string | undefined) {
	if (status === "failed" || status === "cancelled")
		return "destructive" as const;
	if (
		[
			"queued",
			"running",
			"enqueued",
			"waiting",
			"cancel_requested",
		].includes(status ?? "")
	)
		return "warning" as const;
	if (status === "succeeded") return "secondary" as const;
	return "outline" as const;
}

function statusClassName(status: string | undefined) {
	if (status === "succeeded") {
		return "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400";
	}
	if (
		[
			"queued",
			"running",
			"enqueued",
			"waiting",
			"cancel_requested",
		].includes(status ?? "")
	) {
		return "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400";
	}
	return undefined;
}

function formatStatus(status: string | undefined) {
	if (!status) return "Not run";
	return status
		.split("_")
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

function containerMemoryChange(
	startBytes: number | null | undefined,
	peakBytes: number | null | undefined,
) {
	if (startBytes == null || peakBytes == null) return null;
	return Math.max(0, peakBytes - startBytes);
}

function formatContainerMemoryChange(
	startBytes: number | null | undefined,
	peakBytes: number | null | undefined,
) {
	const change = containerMemoryChange(startBytes, peakBytes);
	return change == null ? "—" : formatBytes(change);
}

export function schedulerRecommendations(
	data: SchedulerDiagnosticsResponse,
): string[] {
	const { capacity } = data;
	const recommendations: string[] = [];
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
	if (
		capacity.jobs_queued > 0 &&
		capacity.slots_total > 0 &&
		capacity.slots_running >= capacity.slots_total &&
		(capacity.oldest_queued_seconds ?? 0) >= 60
	) {
		recommendations.push(
			`All ${capacity.slots_total} scheduler slots are busy and the oldest job has waited ${Math.round((capacity.oldest_queued_seconds ?? 0) / 60)} minute(s). Add scheduler replicas.`,
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
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}
	if (query.error || !data) {
		return (
			<Alert variant="destructive">
				<AlertTitle>Scheduler diagnostics unavailable</AlertTitle>
				<AlertDescription>
					Check the API and scheduler logs, then try again.
				</AlertDescription>
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
				<div className="flex items-center justify-between gap-4">
					<div>
						<div className="flex items-center gap-2">
							<h2 className="text-lg font-semibold">Scheduler</h2>
							<Badge
								variant={
									data.leader.healthy
										? "outline"
										: "destructive"
								}
								className={
									data.leader.healthy
										? "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400"
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
									className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`}
								/>
							</Button>
						</TooltipTrigger>
						<TooltipContent>Refresh</TooltipContent>
					</Tooltip>
				</div>

				{recommendations.length > 0 ? (
					<Alert className="border-amber-500/50">
						<AlertTriangle className="h-4 w-4 text-amber-600" />
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
					<Alert className="border-green-500/40">
						<Activity className="h-4 w-4 text-green-600" />
						<AlertTitle>Capacity looks healthy</AlertTitle>
						<AlertDescription>
							No sustained queue or memory pressure is visible in
							this snapshot.
						</AlertDescription>
					</Alert>
				)}

				<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					<StatCard
						title="Replicas"
						value={`${data.capacity.replicas_online}`}
						detail={`${data.capacity.slots_running} of ${data.capacity.slots_total} job slots busy`}
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
					<DataTable
						className={
							data.replicas.length === 0 ? "hidden" : undefined
						}
					>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Replica</DataTableHead>
								<DataTableHead>Role</DataTableHead>
								<DataTableHead>Status</DataTableHead>
								<DataTableHead>Memory</DataTableHead>
								<DataTableHead>Slots</DataTableHead>
								<DataTableHead>Workload</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{data.replicas.map((replica) => (
								<DataTableRow key={replica.id}>
									<DataTableCell>
										<div className="font-medium">
											{replica.hostname}
										</div>
										<div
											className="max-w-[220px] truncate text-xs text-muted-foreground"
											title={replica.id}
										>
											{replica.id}
										</div>
									</DataTableCell>
									<DataTableCell>
										<Badge
											variant="outline"
											className={
												replica.is_leader
													? "border-violet-500/30 bg-violet-500/15 text-violet-700 dark:text-violet-400"
													: "border-blue-500/30 bg-blue-500/15 text-blue-700 dark:text-blue-400"
											}
										>
											{replica.is_leader
												? "Trigger Leader"
												: "Job Runner"}
										</Badge>
									</DataTableCell>
									<DataTableCell>
										<Badge
											variant={
												replica.online
													? "outline"
													: "destructive"
											}
											className={
												replica.online
													? "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400"
													: undefined
											}
										>
											{replica.online
												? "Online"
												: "Stale"}
										</Badge>
										<div className="mt-1 text-xs text-muted-foreground">
											{relativeTime(
												replica.last_heartbeat_at,
											)}
										</div>
									</DataTableCell>
									<DataTableCell>
										{formatBytes(
											replica.memory_current_bytes,
										)}{" "}
										/{" "}
										{formatBytes(
											replica.memory_limit_bytes,
										)}
									</DataTableCell>
									<DataTableCell>
										{replica.active_platform_jobs} /{" "}
										{replica.job_slots}
									</DataTableCell>
									<DataTableCell>
										{replica.active_platform_jobs === 0
											? "Idle"
											: `${replica.active_platform_jobs} running`}
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
					{data.replicas.length === 0 && (
						<p className="rounded-2xl border border-dashed py-8 text-center text-sm text-muted-foreground">
							No scheduler replicas have reported a heartbeat.
						</p>
					)}
				</section>

				<Tabs defaultValue="platform-jobs" className="space-y-3">
					<TabsList>
						<TabsTrigger value="platform-jobs">
							Platform Jobs
						</TabsTrigger>
						<TabsTrigger value="system-schedules">
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
							<DataTable
								className={
									data.tasks.length === 0
										? "hidden"
										: "max-h-[min(56vh,620px)]"
								}
							>
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead>Name</DataTableHead>
										<DataTableHead>State</DataTableHead>
										<DataTableHead>Schedule</DataTableHead>
										<DataTableHead>Next Run</DataTableHead>
										<DataTableHead>Last Run</DataTableHead>
										<DataTableHead title="Change in the shared scheduler container working set while this job ran">
											Memory
										</DataTableHead>
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{data.tasks.map((task) => (
										<DataTableRow
											key={task.task_id}
											clickable
											className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
											tabIndex={0}
											aria-label={`View recent runs for ${task.name}`}
											onClick={() =>
												setSelectedTask(task)
											}
											onKeyDown={(event) => {
												if (
													event.key === "Enter" ||
													event.key === " "
												) {
													event.preventDefault();
													setSelectedTask(task);
												}
											}}
										>
											<DataTableCell>
												<div className="font-medium">
													{task.name}
												</div>
												<div className="text-xs text-muted-foreground">
													{task.execution_mode ===
													"durable_job"
														? "Distributed Job"
														: "Leader Trigger"}
												</div>
											</DataTableCell>
											<DataTableCell>
												<Badge
													variant={statusVariant(
														task.last_run
															?.platform_job_status ??
															task.last_run
																?.status,
													)}
													className={statusClassName(
														task.last_run
															?.platform_job_status ??
															task.last_run
																?.status,
													)}
												>
													{formatStatus(
														task.last_run
															?.platform_job_status ??
															task.last_run
																?.status,
													)}
												</Badge>
												{task.last_run
													?.error_message && (
													<div
														className="mt-1 max-w-[260px] truncate text-xs text-destructive"
														title={
															task.last_run
																.error_message
														}
													>
														{
															task.last_run
																.error_message
														}
													</div>
												)}
											</DataTableCell>
											<DataTableCell>
												<div>{task.schedule}</div>
											</DataTableCell>
											<DataTableCell>
												{relativeTime(task.next_run_at)}
											</DataTableCell>
											<DataTableCell>
												<div>
													{task.last_run
														?.duration_ms == null
														? "—"
														: `${task.last_run.duration_ms} ms`}
												</div>
												<div className="text-xs text-muted-foreground">
													{relativeTime(
														task.last_run
															?.completed_at,
													)}
												</div>
											</DataTableCell>
											<DataTableCell>
												{formatContainerMemoryChange(
													task.last_run
														?.platform_job_memory_start_bytes,
													task.last_run
														?.platform_job_memory_peak_bytes,
												)}
											</DataTableCell>
										</DataTableRow>
									))}
								</DataTableBody>
							</DataTable>
							{data.tasks.length === 0 && (
								<p className="rounded-2xl border border-dashed py-8 text-center text-sm text-muted-foreground">
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
