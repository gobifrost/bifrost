import { PlatformJobRecord } from "./PlatformJobRecord";
import { useDeferredValue, useEffect, useState } from "react";
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
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Input } from "@/components/ui/input";

import { Progress } from "@/components/ui/progress";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { copyToClipboard } from "@/lib/clipboard";
import { KubernetesIcon } from "@/components/icons/KubernetesIcon";
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
const PAGE_SIZE = 25;

type StatusFilter = "all" | "active" | PlatformJob["status"];

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
		return "border-[var(--bf-success)]/30 bg-[var(--bf-success)]/10 text-[var(--bf-success)]";
	}
	if (status === "running" || status === "waiting") {
		return "border-primary/30 bg-primary/10 text-primary";
	}
	if (status === "queued" || status === "cancel_requested") {
		return "border-[var(--bf-warning)]/30 bg-[var(--bf-warning)]/10 text-[var(--bf-warning)]";
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
		return (
			<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
		);
	return <Clock3 className="h-3.5 w-3.5" />;
}

function BackendBadge({ backend }: { backend?: string | null }) {
	if (backend !== "kubernetes") return null;
	return (
		<Badge
			variant="outline"
			className="gap-1"
			title="This job runs in a Kubernetes build pod, not the scheduler"
		>
			<KubernetesIcon className="h-3.5 w-3.5" />
			Kubernetes
		</Badge>
	);
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
			<div className="text-[var(--bf-warning)]">
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
	const [selectedJob, setSelectedJob] =
		useState<ObservablePlatformJob | null>(null);
	const [cancelJobId, setCancelJobId] = useState<string | null>(null);
	const [page, setPage] = useState(0);
	const [search, setSearch] = useState("");
	const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
	const deferredSearch = useDeferredValue(search.trim());
	const queryKey = [
		"platform-jobs",
		"scheduler-diagnostics",
		page,
		deferredSearch,
		statusFilter,
	] as const;
	const query = useQuery({
		queryKey,
		queryFn: ({ signal }) =>
			getPlatformJobs({
				activeOnly: statusFilter === "active",
				limit: PAGE_SIZE,
				offset: page * PAGE_SIZE,
				status:
					statusFilter === "all" || statusFilter === "active"
						? undefined
						: statusFilter,
				search: deferredSearch || undefined,
				signal,
			}),
		refetchOnWindowFocus: true,
		staleTime: 30_000,
		placeholderData: (previousData, previousQuery) =>
			previousQuery?.queryKey[3] === deferredSearch &&
			previousQuery.queryKey[4] === statusFilter
				? previousData
				: undefined,
	});

	useEffect(
		() =>
			webSocketService.onAnyPlatformJobUpdate((job) => {
				setSelectedJob((current) =>
					current?.id === job.id && job.revision >= current.revision
						? job
						: current,
				);
				void queryClient.invalidateQueries({
					queryKey: ["platform-jobs"],
				});
			}),
		[queryClient],
	);

	const jobs = query.data?.jobs ?? [];
	const total = query.data?.total ?? 0;
	const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
	const displayedPage = Math.floor((query.data?.offset ?? 0) / PAGE_SIZE);
	const hasFilters = statusFilter !== "all" || deferredSearch.length > 0;
	const cancelMutation = useMutation({
		mutationFn: cancelPlatformJob,
		onSuccess: (response) => {
			setSelectedJob(response.job);
			void queryClient.invalidateQueries({
				queryKey: ["platform-jobs"],
			});
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

	const handleSearchChange = (value: string) => {
		setSearch(value);
		setPage(0);
	};

	const handleStatusChange = (value: StatusFilter) => {
		setStatusFilter(value);
		setPage(0);
	};

	return (
		<>
			<section
				aria-labelledby="platform-jobs-heading"
				className="space-y-3"
			>
				<div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
					<div>
						<div className="flex flex-wrap items-center gap-2">
							<h3
								id="platform-jobs-heading"
								className="font-semibold"
							>
								Platform Jobs
							</h3>
							{query.data ? (
								<Badge variant="outline">
									{total} {total === 1 ? "job" : "jobs"}
								</Badge>
							) : null}
						</div>
						<p className="mt-1 text-sm text-muted-foreground">
							On-demand and scheduled durable work, updated live.
						</p>
					</div>
					<div className="flex min-w-0 w-full flex-col gap-2 sm:flex-row sm:flex-wrap lg:w-auto">
						<label
							className="sr-only"
							htmlFor="platform-job-search"
						>
							Search Platform Jobs
						</label>
						<Input
							id="platform-job-search"
							className="min-h-11 min-w-0 sm:flex-1 lg:w-64"
							placeholder="Search jobs"
							value={search}
							onChange={(event) =>
								handleSearchChange(event.target.value)
							}
						/>
						<Select
							value={statusFilter}
							onValueChange={handleStatusChange}
						>
							<SelectTrigger
								className="min-h-11 w-full sm:w-44"
								aria-label="Filter Platform Jobs by state"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="all">All States</SelectItem>
								<SelectItem value="active">Active</SelectItem>
								<SelectItem value="queued">Queued</SelectItem>
								<SelectItem value="running">Running</SelectItem>
								<SelectItem value="waiting">Waiting</SelectItem>
								<SelectItem value="cancel_requested">
									Cancel Requested
								</SelectItem>
								<SelectItem value="succeeded">
									Succeeded
								</SelectItem>
								<SelectItem value="failed">Failed</SelectItem>
								<SelectItem value="cancelled">
									Cancelled
								</SelectItem>
							</SelectContent>
						</Select>
					</div>
				</div>

				{query.error && (
					<Alert variant="destructive">
						<AlertDescription>
							{query.data
								? "Platform jobs could not refresh. Showing the last available snapshot."
								: "Platform jobs could not be loaded."}
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
							{query.isFetching ? "Retrying…" : "Retry jobs"}
						</Button>
					</Alert>
				)}
				{query.isLoading ? (
					<div
						role="status"
						className="flex items-center justify-center gap-3 py-10 text-sm text-muted-foreground"
					>
						<Loader2
							aria-hidden="true"
							className="h-5 w-5 animate-spin motion-reduce:animate-none"
						/>
						Loading platform jobs…
					</div>
				) : query.error && !query.data ? null : jobs.length === 0 ? (
					<div className="rounded-[var(--bf-radius-surface)] border border-dashed px-5 py-10 text-center">
						<CheckCircle2 className="mx-auto h-6 w-6 text-[var(--bf-success)]" />
						<p className="mt-3 font-medium">
							{hasFilters
								? "No Platform Jobs Match"
								: "No Platform Jobs Yet"}
						</p>
						<p className="mt-1 text-sm text-muted-foreground">
							{hasFilters
								? "Try another search or state filter."
								: "Builds, deploys, maintenance, and other durable work will appear here."}
						</p>
					</div>
				) : (
					<div className="@container space-y-4">
						<ul
							aria-label="Platform jobs"
							className="space-y-3 @4xl:hidden"
						>
							{jobs.map((job) => (
								<PlatformJobRecord
									key={job.id}
									job={job}
									onSelect={() => setSelectedJob(job)}
									status={
										<span className="flex flex-wrap items-center gap-2">
											<Badge
												variant="outline"
												className={statusClassName(
													job.status,
												)}
											>
												<StatusIcon status={job.status} />
												{displayStatus(job.status)}
											</Badge>
											<BackendBadge
												backend={job.execution_backend}
											/>
										</span>
									}
									memory={
										<MemorySummary
											job={job}
											availableMemoryBytes={
												availableMemoryBytes
											}
										/>
									}
									elapsed={
										<>
											{elapsed(job)}
											<p className="text-xs text-muted-foreground">
												{ACTIVE_STATUSES.has(job.status)
													? "in progress"
													: relativeFinished(job)}
											</p>
										</>
									}
								/>
							))}
						</ul>
						<DataTable className="hidden @4xl:flex max-h-[min(56vh,620px)]">
							<DataTableHeader>
								<DataTableRow>
									<DataTableHead>Name</DataTableHead>
									<DataTableHead>State</DataTableHead>
									<DataTableHead>Elapsed</DataTableHead>
									<DataTableHead>Memory</DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{jobs.map((job) => (
									<DataTableRow
										key={job.id}
										clickable
										tabIndex={0}
										className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
										aria-label={`View ${job.title} platform job`}
										onClick={() => setSelectedJob(job)}
										onKeyDown={(event) => {
											if (
												event.key === "Enter" ||
												event.key === " "
											) {
												event.preventDefault();
												setSelectedJob(job);
											}
										}}
									>
										<DataTableCell>
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
												title={job.requested_by_name}
											>
												{job.requested_by_name}
											</p>
										</DataTableCell>
									<DataTableCell>
										<div className="flex flex-wrap items-center gap-1">
											<Badge
												variant="outline"
												className={`gap-1 ${statusClassName(job.status)}`}
											>
												<StatusIcon
													status={job.status}
												/>
												{displayStatus(job.status)}
											</Badge>
											<BackendBadge
												backend={job.execution_backend}
											/>
										</div>
											<p
												className="mt-1 max-w-[260px] truncate text-xs text-muted-foreground"
												title={
													job.progress.phase ??
													undefined
												}
											>
												{job.progress.phase ??
													"No phase reported"}
											</p>
											{job.progress.percent != null &&
											ACTIVE_STATUSES.has(job.status) ? (
												<div className="mt-1.5 flex items-center gap-2">
													<Progress
														className="h-1.5 w-24"
														value={
															job.progress.percent
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
													title={job.error.message}
												>
													{job.error.message}
												</p>
											) : null}
										</DataTableCell>
										<DataTableCell>
											<p>{elapsed(job)}</p>
											<p className="text-xs text-muted-foreground">
												{ACTIVE_STATUSES.has(job.status)
													? "in progress"
													: relativeFinished(job)}
											</p>
										</DataTableCell>
										<DataTableCell>
											<MemorySummary
												job={job}
												availableMemoryBytes={
													availableMemoryBytes
												}
											/>
										</DataTableCell>
									</DataTableRow>
								))}
							</DataTableBody>
						</DataTable>
						<div className="flex flex-wrap items-center justify-between gap-3 text-sm">
							<p className="text-muted-foreground">
								{(query.data?.offset ?? 0) + 1}–
								{Math.min(
									(query.data?.offset ?? 0) + jobs.length,
									total,
								)}{" "}
								of {total}
							</p>
							<nav
								aria-label="Platform job pages"
								className="flex w-full flex-wrap items-center justify-between gap-2 sm:w-auto"
							>
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									aria-label="Go to previous page"
									disabled={page === 0 || query.isFetching}
									onClick={() =>
										setPage((current) =>
											Math.max(0, current - 1),
										)
									}
								>
									Previous
								</Button>
								<span
									aria-live="polite"
									className="text-xs text-muted-foreground"
								>
									Page {displayedPage + 1} of {totalPages}
								</span>
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									aria-label="Go to next page"
									disabled={
										page + 1 >= totalPages ||
										query.isFetching
									}
									onClick={() =>
										setPage((current) => current + 1)
									}
								>
									Next
								</Button>
							</nav>
						</div>
					</div>
				)}
			</section>

			<Sheet
				open={selectedJob != null}
				onOpenChange={(open) => !open && setSelectedJob(null)}
			>
				<SheetContent
					side="right"
					className="w-full overflow-hidden p-0 sm:max-w-xl"
				>
					<SheetHeader className="shrink-0 border-b p-[var(--bf-surface-pad)] pr-14">
						<SheetTitle className="[overflow-wrap:anywhere]">
							{selectedJob?.title ?? "Platform Job"}
						</SheetTitle>
						<SheetDescription>
							Durable status, admission details, and execution
							history for this operation.
						</SheetDescription>
					</SheetHeader>
					{selectedJob ? (
						<div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-[var(--bf-surface-pad)]">
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
									className="h-auto max-w-full whitespace-normal font-mono font-normal [overflow-wrap:anywhere]"
								>
									{selectedJob.job_type}
								</Badge>
								<BackendBadge
									backend={selectedJob.execution_backend}
								/>
							</div>

							<div className="mt-5 rounded-[var(--bf-radius-surface)] border bg-muted/20 px-3 py-2">
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
										className="min-h-11 min-w-11"
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
								<p className="mt-2 text-sm [overflow-wrap:anywhere]">
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
										<p className="[overflow-wrap:anywhere]">
											{selectedJob.error.message}
										</p>
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
									label="Runs on"
									value={
										selectedJob.execution_backend ===
										"kubernetes"
											? "Kubernetes build pod"
											: "Scheduler"
									}
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
								{selectedJob.execution_backend === "kubernetes"
									? "Memory samples describe the isolated build pod cgroup while this job ran."
									: "Memory samples describe the shared scheduler cgroup while this job ran; they are not isolated process usage."}
							</p>

							<div className="mt-6 flex flex-wrap gap-2">
								{selectedJob.action_url ? (
									<Button
										asChild
										variant="outline"
										className="min-h-11"
									>
										<Link to={selectedJob.action_url}>
											Open resource{" "}
											<ArrowUpRight className="ml-2 h-4 w-4" />
										</Link>
									</Button>
								) : null}
								{selectedJob.can_cancel ? (
									<Button
										variant="destructive"
										type="button"
										className="min-h-11"
										onClick={() => {
											cancelMutation.reset();
											setCancelJobId(selectedJob.id);
										}}
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
				onOpenChange={(open) => {
					if (!open && !cancelMutation.isPending)
						setCancelJobId(null);
				}}
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
					{cancelMutation.isError && (
						<p role="alert" className="text-sm text-destructive">
							Cancellation failed. The job is still selected; try
							again.
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							className="min-h-11 lg:min-h-11"
							disabled={cancelMutation.isPending}
						>
							Keep job
						</AlertDialogCancel>
						<AlertDialogAction
							type="button"
							className="min-h-11"
							variant="destructive"
							disabled={cancelMutation.isPending}
							onClick={(event) => {
								event.preventDefault();
								if (cancelJobId && !cancelMutation.isPending)
									cancelMutation.mutate(cancelJobId);
							}}
						>
							{cancelMutation.isPending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : null}
							{cancelMutation.isPending
								? "Cancelling…"
								: cancelMutation.isError
									? "Retry cancellation"
									: "Cancel job"}
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
				className={`mt-1 [overflow-wrap:anywhere] font-medium ${mono ? "font-mono text-xs" : ""}`}
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
