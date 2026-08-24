import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format, formatDistanceStrict } from "date-fns";
import {
	AlertCircle,
	ArrowUpRight,
	Ban,
	CheckCircle2,
	Clock3,
	Copy,
	Loader2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { copyToClipboard } from "@/lib/clipboard";
import {
	cancelPlatformJob,
	getPlatformJobs,
	type PlatformJob,
} from "@/services/platformJobs";
import { webSocketService } from "@/services/websocket";

type ObservablePlatformJob = PlatformJob & {
	memory_required_bytes?: number | null;
};

const ACTIVE_STATUSES = new Set([
	"queued",
	"running",
	"waiting",
	"cancel_requested",
]);
const JOBS_QUERY_KEY = ["platform-jobs", "scheduler-diagnostics"] as const;

function formatBytes(value: number | null | undefined) {
	if (value == null) return "Not recorded";
	const units = ["B", "KiB", "MiB", "GiB", "TiB"];
	let amount = value;
	let unit = 0;
	while (amount >= 1024 && unit < units.length - 1) {
		amount /= 1024;
		unit += 1;
	}
	return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function displayStatus(status: string) {
	return status
		.split("_")
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

function statusClassName(status: string) {
	if (status === "succeeded") {
		return "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400";
	}
	if (status === "running" || status === "waiting") {
		return "border-blue-500/30 bg-blue-500/15 text-blue-700 dark:text-blue-400";
	}
	if (status === "queued" || status === "cancel_requested") {
		return "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400";
	}
	if (status === "failed" || status === "cancelled") {
		return "border-destructive/30 bg-destructive/10 text-destructive";
	}
	return "";
}

function StatusIcon({ status }: { status: string }) {
	if (status === "succeeded") return <CheckCircle2 className="h-3.5 w-3.5" />;
	if (status === "failed" || status === "cancelled") {
		return <AlertCircle className="h-3.5 w-3.5" />;
	}
	if (ACTIVE_STATUSES.has(status))
		return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
	return <Clock3 className="h-3.5 w-3.5" />;
}

function sortJobs(jobs: ObservablePlatformJob[]) {
	return [...jobs].sort((left, right) => {
		const activeDifference =
			Number(ACTIVE_STATUSES.has(right.status)) -
			Number(ACTIVE_STATUSES.has(left.status));
		if (activeDifference) return activeDifference;
		return (
			new Date(right.created_at).getTime() -
			new Date(left.created_at).getTime()
		);
	});
}

function mergeJob(
	jobs: ObservablePlatformJob[] | undefined,
	job: ObservablePlatformJob,
) {
	const byId = new Map((jobs ?? []).map((item) => [item.id, item]));
	const current = byId.get(job.id);
	if (!current || job.revision >= current.revision) byId.set(job.id, job);
	const sorted = sortJobs([...byId.values()]);
	const active = sorted.filter((item) => ACTIVE_STATUSES.has(item.status));
	const recent = sorted
		.filter((item) => !ACTIVE_STATUSES.has(item.status))
		.slice(0, 50);
	return [...active, ...recent];
}

async function loadVisibleJobs(signal?: AbortSignal) {
	const [active, recent] = await Promise.all([
		getPlatformJobs({ activeOnly: true, limit: 200, signal }),
		getPlatformJobs({ activeOnly: false, limit: 50, signal }),
	]);
	let merged: ObservablePlatformJob[] = [];
	for (const job of [...active, ...recent]) merged = mergeJob(merged, job);
	return merged;
}

function elapsed(job: ObservablePlatformJob) {
	const start = new Date(job.started_at ?? job.created_at);
	const end = job.completed_at ? new Date(job.completed_at) : new Date();
	return formatDistanceStrict(start, end);
}

function memoryDelta(job: ObservablePlatformJob) {
	if (job.memory_start_bytes == null || job.memory_peak_bytes == null)
		return null;
	return Math.max(0, job.memory_peak_bytes - job.memory_start_bytes);
}

function MemorySummary({
	job,
	availableMemoryBytes,
}: {
	job: ObservablePlatformJob;
	availableMemoryBytes: number | null;
}) {
	const required = job.memory_required_bytes;
	const waitingForMemory =
		job.status === "queued" &&
		job.progress.phase?.toLowerCase().includes("scheduler memory");
	if (waitingForMemory && required != null) {
		return (
			<div className="text-amber-700 dark:text-amber-400">
				<p className="font-medium">
					{formatBytes(availableMemoryBytes)} available
				</p>
				<p className="text-xs">{formatBytes(required)} required</p>
			</div>
		);
	}
	const delta = memoryDelta(job);
	if (delta != null) {
		return (
			<div>
				<p className="font-medium">+{formatBytes(delta)}</p>
				<p className="text-xs text-muted-foreground">
					shared container change
				</p>
			</div>
		);
	}
	return required == null ? (
		<span className="text-muted-foreground">—</span>
	) : (
		<div>
			<p className="font-medium">{formatBytes(required)}</p>
			<p className="text-xs text-muted-foreground">
				admission requirement
			</p>
		</div>
	);
}

interface PlatformJobsPanelProps {
	availableMemoryBytes: number | null;
}

export function PlatformJobsPanel({
	availableMemoryBytes,
}: PlatformJobsPanelProps) {
	const queryClient = useQueryClient();
	const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
	const [cancelJobId, setCancelJobId] = useState<string | null>(null);
	const query = useQuery({
		queryKey: JOBS_QUERY_KEY,
		queryFn: ({ signal }) => loadVisibleJobs(signal),
		refetchOnWindowFocus: true,
		staleTime: 30_000,
	});

	useEffect(
		() =>
			webSocketService.onAnyPlatformJobUpdate((job) => {
				queryClient.setQueryData<ObservablePlatformJob[]>(
					JOBS_QUERY_KEY,
					(current) => mergeJob(current, job),
				);
			}),
		[queryClient],
	);

	const jobs = query.data ?? [];
	const selectedJob = jobs.find((job) => job.id === selectedJobId) ?? null;
	const activeCount = jobs.filter((job) =>
		ACTIVE_STATUSES.has(job.status),
	).length;
	const cancelMutation = useMutation({
		mutationFn: cancelPlatformJob,
		onSuccess: (response) => {
			queryClient.setQueryData<ObservablePlatformJob[]>(
				JOBS_QUERY_KEY,
				(current) => mergeJob(current, response.job),
			);
			setCancelJobId(null);
			if (response.accepted) toast.success("Cancellation requested");
			else toast.info("This job can no longer be cancelled");
		},
		onError: () => toast.error("Failed to cancel platform job"),
	});

	const handleCopyJobId = async () => {
		if (!selectedJob) return;
		if (await copyToClipboard(selectedJob.id))
			toast.success("Job ID copied");
		else toast.error("Failed to copy job ID");
	};

	return (
		<>
			<section aria-labelledby="platform-jobs-heading">
				<Card>
					<CardHeader>
						<div>
							<div className="flex flex-wrap items-center gap-2">
								<CardTitle
									id="platform-jobs-heading"
									className="text-base"
								>
									Platform Jobs
								</CardTitle>
								<Badge
									variant={
										activeCount ? "warning" : "outline"
									}
								>
									{activeCount} active
								</Badge>
							</div>
							<p className="mt-1 text-sm text-muted-foreground">
								On-demand and scheduled durable work, updated
								live.
							</p>
						</div>
					</CardHeader>
					<CardContent>
						{query.isLoading ? (
							<div className="flex justify-center py-10">
								<Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
							</div>
						) : query.error ? (
							<Alert variant="destructive">
								<AlertDescription>
									Platform jobs could not be loaded. Use
									Refresh above to try again.
								</AlertDescription>
							</Alert>
						) : jobs.length === 0 ? (
							<div className="rounded-lg border border-dashed px-5 py-10 text-center">
								<CheckCircle2 className="mx-auto h-6 w-6 text-green-600" />
								<p className="mt-3 font-medium">
									No Platform Jobs Yet
								</p>
								<p className="mt-1 text-sm text-muted-foreground">
									Builds, deploys, maintenance, and other
									durable work will appear here.
								</p>
							</div>
						) : (
							<div className="max-h-[min(56vh,620px)] overflow-auto rounded-lg border">
								<Table className="min-w-[760px]">
									<TableHeader className="sticky top-0 z-10 bg-card">
										<TableRow>
											<TableHead>Name</TableHead>
											<TableHead>State</TableHead>
											<TableHead>Elapsed</TableHead>
											<TableHead>Memory</TableHead>
										</TableRow>
									</TableHeader>
									<TableBody>
										{jobs.map((job) => (
											<TableRow
												key={job.id}
												tabIndex={0}
												className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
												aria-label={`View ${job.title} platform job`}
												onClick={() =>
													setSelectedJobId(job.id)
												}
												onKeyDown={(event) => {
													if (
														event.key === "Enter" ||
														event.key === " "
													) {
														event.preventDefault();
														setSelectedJobId(
															job.id,
														);
													}
												}}
											>
												<TableCell>
													<p
														className="max-w-[240px] truncate font-medium"
														title={job.title}
													>
														{job.title}
													</p>
													<p className="font-mono text-xs text-muted-foreground">
														{job.job_type}
													</p>
													<p
														className="max-w-[240px] truncate text-xs text-muted-foreground"
														title={
															job.requested_by_name
														}
													>
														{job.requested_by_name}
													</p>
												</TableCell>
												<TableCell>
													<Badge
														variant="outline"
														className={`gap-1 ${statusClassName(job.status)}`}
													>
														<StatusIcon
															status={job.status}
														/>
														{displayStatus(
															job.status,
														)}
													</Badge>
													<p
														className="mt-1 max-w-[260px] truncate text-xs text-muted-foreground"
														title={
															job.progress
																.phase ??
															undefined
														}
													>
														{job.progress.phase ??
															"No phase reported"}
													</p>
													{job.progress.percent !=
														null &&
													ACTIVE_STATUSES.has(
														job.status,
													) ? (
														<div className="mt-1.5 flex items-center gap-2">
															<Progress
																className="h-1.5 w-24"
																value={
																	job.progress
																		.percent
																}
															/>
															<span className="text-xs text-muted-foreground">
																{job.progress.percent.toFixed(
																	0,
																)}
																%
															</span>
														</div>
													) : null}
													{job.error ? (
														<p
															className="mt-1 max-w-[260px] truncate text-xs text-destructive"
															title={
																job.error
																	.message
															}
														>
															{job.error.message}
														</p>
													) : null}
												</TableCell>
												<TableCell>
													<p>{elapsed(job)}</p>
													<p className="text-xs text-muted-foreground">
														{ACTIVE_STATUSES.has(
															job.status,
														)
															? "in progress"
															: relativeFinished(
																	job,
																)}
													</p>
												</TableCell>
												<TableCell>
													<MemorySummary
														job={job}
														availableMemoryBytes={
															availableMemoryBytes
														}
													/>
												</TableCell>
											</TableRow>
										))}
									</TableBody>
								</Table>
							</div>
						)}
					</CardContent>
				</Card>
			</section>

			<Sheet
				open={selectedJob != null}
				onOpenChange={(open) => !open && setSelectedJobId(null)}
			>
				<SheetContent
					side="right"
					className="w-full overflow-hidden p-0 sm:max-w-xl"
				>
					<SheetHeader className="border-b px-5 py-4 pr-14">
						<SheetTitle>
							{selectedJob?.title ?? "Platform Job"}
						</SheetTitle>
						<SheetDescription>
							Durable status, admission details, and execution
							history for this operation.
						</SheetDescription>
					</SheetHeader>
					{selectedJob ? (
						<div className="min-h-0 flex-1 overflow-auto p-5">
							<div className="flex flex-wrap items-center gap-2">
								<Badge
									variant="outline"
									className={`gap-1 ${statusClassName(selectedJob.status)}`}
								>
									<StatusIcon status={selectedJob.status} />
									{displayStatus(selectedJob.status)}
								</Badge>
								<Badge
									variant="outline"
									className="font-mono font-normal"
								>
									{selectedJob.job_type}
								</Badge>
							</div>

							<div className="mt-5 rounded-lg border bg-muted/20 px-3 py-2">
								<p className="text-xs font-medium text-muted-foreground">
									Job ID
								</p>
								<div className="mt-1 flex items-center gap-2">
									<p className="min-w-0 flex-1 break-all font-mono text-xs">
										{selectedJob.id}
									</p>
									<Button
										type="button"
										variant="ghost"
										size="icon-sm"
										onClick={handleCopyJobId}
										aria-label="Copy job ID"
										title="Copy job ID"
									>
										<Copy className="h-3.5 w-3.5" />
									</Button>
								</div>
							</div>

							<section className="mt-6">
								<h3 className="text-sm font-semibold">
									Current Phase
								</h3>
								<p className="mt-2 text-sm">
									{selectedJob.progress.phase ??
										"No phase reported"}
								</p>
								{selectedJob.progress.percent != null ? (
									<div className="mt-3 flex items-center gap-3">
										<Progress
											value={selectedJob.progress.percent}
										/>
										<span className="w-10 text-right text-sm font-medium">
											{selectedJob.progress.percent.toFixed(
												0,
											)}
											%
										</span>
									</div>
								) : null}
							</section>

							{selectedJob.error ? (
								<Alert variant="destructive" className="mt-5">
									<AlertDescription>
										<p>{selectedJob.error.message}</p>
										<p className="mt-1 font-mono text-xs">
											{selectedJob.error.code}
										</p>
									</AlertDescription>
								</Alert>
							) : null}

							<dl className="mt-6 grid gap-x-6 gap-y-4 border-y py-5 sm:grid-cols-2">
								<Detail
									label="Requester"
									value={selectedJob.requested_by_name}
								/>
								<Detail
									label="Attempts"
									value={`${selectedJob.attempt} of ${selectedJob.max_attempts}`}
								/>
								<Detail
									label="Created"
									value={format(
										new Date(selectedJob.created_at),
										"MMM d, yyyy, h:mm:ss a",
									)}
								/>
								<Detail
									label="Elapsed"
									value={elapsed(selectedJob)}
								/>
								<Detail
									label="Resource"
									value={
										selectedJob.resource_type ??
										"Not specified"
									}
									mono={selectedJob.resource_type != null}
								/>
								<Detail
									label="Resource ID"
									value={
										selectedJob.resource_id ??
										"Not specified"
									}
									mono={selectedJob.resource_id != null}
								/>
								<Detail
									label="Admission Requirement"
									value={formatBytes(
										selectedJob.memory_required_bytes,
									)}
								/>
								<Detail
									label="Available Scheduler Headroom"
									value={formatBytes(availableMemoryBytes)}
								/>
								<Detail
									label="Container at Start"
									value={formatBytes(
										selectedJob.memory_start_bytes,
									)}
								/>
								<Detail
									label="Observed Container Peak"
									value={formatBytes(
										selectedJob.memory_peak_bytes,
									)}
								/>
							</dl>
							<p className="mt-3 text-xs leading-relaxed text-muted-foreground">
								Memory samples describe the shared scheduler
								cgroup while this job ran; they are not isolated
								process usage.
							</p>

							<div className="mt-6 flex flex-wrap gap-2">
								{selectedJob.action_url ? (
									<Button asChild variant="outline">
										<Link to={selectedJob.action_url}>
											Open resource{" "}
											<ArrowUpRight className="ml-2 h-4 w-4" />
										</Link>
									</Button>
								) : null}
								{selectedJob.can_cancel ? (
									<Button
										variant="destructive"
										onClick={() =>
											setCancelJobId(selectedJob.id)
										}
									>
										<Ban className="mr-2 h-4 w-4" /> Cancel
										job
									</Button>
								) : null}
							</div>
						</div>
					) : null}
				</SheetContent>
			</Sheet>

			<AlertDialog
				open={cancelJobId != null}
				onOpenChange={(open) => !open && setCancelJobId(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Cancel this platform job?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Queued work will be cancelled immediately. Running
							work may finish if its handler cannot be interrupted
							safely.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={cancelMutation.isPending}>
							Keep job
						</AlertDialogCancel>
						<AlertDialogAction
							variant="destructive"
							disabled={cancelMutation.isPending}
							onClick={() =>
								cancelJobId &&
								cancelMutation.mutate(cancelJobId)
							}
						>
							{cancelMutation.isPending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							) : null}
							Cancel job
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}

function Detail({
	label,
	value,
	mono = false,
}: {
	label: string;
	value: string;
	mono?: boolean;
}) {
	return (
		<div className="min-w-0">
			<dt className="text-xs text-muted-foreground">{label}</dt>
			<dd
				className={`mt-1 break-words font-medium ${mono ? "font-mono text-xs" : ""}`}
			>
				{value}
			</dd>
		</div>
	);
}

function relativeFinished(job: ObservablePlatformJob) {
	if (!job.completed_at) return "not completed";
	return `finished ${formatDistanceStrict(new Date(job.completed_at), new Date(), { addSuffix: true })}`;
}
