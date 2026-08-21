import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
	Activity,
	AlertTriangle,
	Box,
	Clock3,
	ExternalLink,
	RefreshCw,
	Server,
	Wrench,
} from "lucide-react";
import { Link } from "react-router-dom";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { getBuilderRunnerSetup } from "@/services/builderRunner";
import { listPlatformJobs, type PlatformJob } from "@/services/platformJobs";
import { cn } from "@/lib/utils";

const BUILDER_JOB_TYPES = new Set([
	"sandbox.runner.provision",
	"solution.builder.turn",
	"solution.build",
	"solution.deploy",
]);

function elapsed(job: PlatformJob): string {
	if (!job.started_at) return "Not started";
	const end = job.completed_at ? new Date(job.completed_at) : new Date();
	const milliseconds = Math.max(
		0,
		end.getTime() - new Date(job.started_at).getTime(),
	);
	if (milliseconds < 1_000) return `${milliseconds} ms`;
	if (milliseconds < 60_000)
		return `${(milliseconds / 1_000).toFixed(1)} sec`;
	return `${Math.floor(milliseconds / 60_000)}m ${Math.round((milliseconds % 60_000) / 1_000)}s`;
}

function relativeTime(value: string | null | undefined): string {
	return value
		? formatDistanceToNow(new Date(value), { addSuffix: true })
		: "Never";
}

function resultRecord(job: PlatformJob): Record<string, unknown> {
	return job.result && typeof job.result === "object" ? job.result : {};
}

function harnessSummary(job: PlatformJob): string | null {
	const diagnostics = resultRecord(job).harness_diagnostics;
	if (!diagnostics || typeof diagnostics !== "object") return null;
	const record = diagnostics as Record<string, unknown>;
	const tools = Number(record.tool_call_count ?? 0);
	const errors = Number(record.tool_error_count ?? 0);
	const compactions = Number(record.compaction_count ?? 0);
	return `${tools} tool call${tools === 1 ? "" : "s"} · ${errors} error${errors === 1 ? "" : "s"} · ${compactions} compaction${compactions === 1 ? "" : "s"}`;
}

function statusTone(status: PlatformJob["status"]): string {
	if (status === "succeeded") {
		return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
	}
	if (status === "failed" || status === "cancelled") {
		return "border-destructive/30 bg-destructive/10 text-destructive";
	}
	return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300";
}

function labelFor(jobType: string): string {
	const labels: Record<string, string> = {
		"sandbox.runner.provision": "Runner test",
		"solution.builder.turn": "Agent turn",
		"solution.build": "App build",
		"solution.deploy": "Deployment",
	};
	return labels[jobType] ?? jobType;
}

export function BuilderTab() {
	const setupQuery = useQuery({
		queryKey: ["admin", "builder", "runner", "diagnostics"],
		queryFn: ({ signal }) => getBuilderRunnerSetup(signal),
		refetchInterval: 10_000,
	});
	const jobsQuery = useQuery({
		queryKey: ["platform-jobs", "builder", "diagnostics"],
		queryFn: ({ signal }) =>
			listPlatformJobs({ activeOnly: false, limit: 100, signal }),
		refetchInterval: 10_000,
	});

	if (setupQuery.isLoading && jobsQuery.isLoading) {
		return (
			<div className="mx-auto max-w-[1100px] space-y-4">
				<Skeleton className="h-24 w-full rounded-2xl" />
				<Skeleton className="h-72 w-full rounded-2xl" />
			</div>
		);
	}

	const setup = setupQuery.data;
	const jobs = (jobsQuery.data ?? []).filter((job) =>
		BUILDER_JOB_TYPES.has(job.job_type),
	);
	const activeJobs = jobs.filter((job) =>
		["queued", "running", "waiting", "cancel_requested"].includes(
			job.status,
		),
	);
	const lastProbe = jobs.find(
		(job) => job.job_type === "sandbox.runner.provision",
	);
	const observedImage = lastProbe
		? String(
				resultRecord(lastProbe).runner_image ??
					setup?.runner_image ??
					"Unknown",
			)
		: (setup?.runner_image ?? "Not tested");
	const readiness = setup?.readiness;
	const healthLabel = readiness?.ready
		? "Ready for users"
		: readiness?.connected
			? "Connected, not enabled"
			: "Needs attention";

	return (
		<div
			className="mx-auto max-w-[1100px] space-y-6"
			data-testid="builder-diagnostics"
		>
			<div className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<div className="flex items-center gap-2">
						<h2 className="text-lg font-semibold">Builder</h2>
						<Badge
							variant="outline"
							className={cn(
								readiness?.connected
									? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
									: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
							)}
						>
							{healthLabel}
						</Badge>
					</div>
					<p className="mt-1 text-sm text-muted-foreground">
						Runner connectivity, agent harness health, app builds,
						and deployments.
					</p>
				</div>
				<div className="flex items-center gap-2">
					<Button asChild variant="outline" size="sm">
						<Link to="/settings/builder">
							<Wrench className="h-4 w-4" /> Setup
						</Link>
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled={setupQuery.isFetching || jobsQuery.isFetching}
						onClick={() => {
							void setupQuery.refetch();
							void jobsQuery.refetch();
						}}
					>
						<RefreshCw
							className={cn(
								"h-4 w-4",
								(setupQuery.isFetching ||
									jobsQuery.isFetching) &&
									"animate-spin",
							)}
						/>
						Refresh
					</Button>
				</div>
			</div>

			{setupQuery.isError || jobsQuery.isError ? (
				<Alert variant="destructive">
					<AlertTriangle className="h-4 w-4" />
					<AlertTitle>
						Some Builder diagnostics are unavailable
					</AlertTitle>
					<AlertDescription>
						Check API connectivity, then refresh this tab. Available
						data is still shown below.
					</AlertDescription>
				</Alert>
			) : null}

			<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
				<Card>
					<CardContent className="pt-5">
						<Server className="h-5 w-5 text-muted-foreground" />
						<p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Execution
						</p>
						<p className="mt-1 text-lg font-semibold capitalize">
							{readiness?.provider ?? "Not configured"}
						</p>
						<p className="mt-1 text-xs text-muted-foreground">
							{readiness?.connected
								? "Live probe passed"
								: "No live connection"}
						</p>
					</CardContent>
				</Card>
				<Card>
					<CardContent className="pt-5">
						<Clock3 className="h-5 w-5 text-muted-foreground" />
						<p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Last runner test
						</p>
						<p className="mt-1 text-lg font-semibold">
							{lastProbe ? elapsed(lastProbe) : "Not run"}
						</p>
						<p className="mt-1 text-xs text-muted-foreground">
							{relativeTime(
								lastProbe?.completed_at ??
									lastProbe?.updated_at,
							)}
						</p>
					</CardContent>
				</Card>
				<Card>
					<CardContent className="pt-5">
						<Activity className="h-5 w-5 text-muted-foreground" />
						<p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Active work
						</p>
						<p className="mt-1 text-lg font-semibold">
							{activeJobs.length}
						</p>
						<p className="mt-1 text-xs text-muted-foreground">
							Turns, builds, deployments, and setup
						</p>
					</CardContent>
				</Card>
				<Card>
					<CardContent className="pt-5">
						<Box className="h-5 w-5 text-muted-foreground" />
						<p className="mt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Runner image
						</p>
						<p
							className="mt-1 truncate font-mono text-sm font-semibold"
							title={observedImage}
						>
							{observedImage}
						</p>
						<p className="mt-1 text-xs text-muted-foreground">
							Observed by the latest setup job
						</p>
					</CardContent>
				</Card>
			</div>

			<Card>
				<CardHeader>
					<CardTitle className="text-base">
						Recent Builder operations
					</CardTitle>
				</CardHeader>
				<CardContent className="space-y-3">
					{jobs.length === 0 ? (
						<div className="py-8 text-center">
							<Activity className="mx-auto h-8 w-8 text-muted-foreground/50" />
							<p className="mt-3 text-sm font-medium">
								No Builder operations yet
							</p>
							<p className="mt-1 text-xs text-muted-foreground">
								Run the connection test or start a build to
								populate diagnostics.
							</p>
						</div>
					) : (
						jobs.map((job) => {
							const harness = harnessSummary(job);
							return (
								<div
									key={job.id}
									className="rounded-2xl border p-4"
								>
									<div className="flex flex-wrap items-start justify-between gap-3">
										<div className="min-w-0">
											<div className="flex flex-wrap items-center gap-2">
												<p className="font-medium">
													{job.title}
												</p>
												<Badge variant="outline">
													{labelFor(job.job_type)}
												</Badge>
												<Badge
													variant="outline"
													className={statusTone(
														job.status,
													)}
												>
													{job.status.replaceAll(
														"_",
														" ",
													)}
												</Badge>
											</div>
											<p className="mt-1 text-xs text-muted-foreground">
												{job.requested_by_name} ·{" "}
												{relativeTime(job.created_at)} ·{" "}
												{elapsed(job)}
												{job.external_provider
													? ` · ${job.external_provider}`
													: ""}
											</p>
										</div>
										{job.action_url ? (
											<Button
												asChild
												variant="ghost"
												size="sm"
											>
												<Link to={job.action_url}>
													Open{" "}
													<ExternalLink className="h-3.5 w-3.5" />
												</Link>
											</Button>
										) : null}
									</div>
									{job.status !== "succeeded" &&
									job.progress.percent != null ? (
										<Progress
											value={job.progress.percent}
											className="mt-3 h-1.5"
										/>
									) : null}
									<p className="mt-3 text-xs text-muted-foreground">
										{job.error?.message ??
											job.progress.phase ??
											"No additional diagnostic detail"}
									</p>
									{harness ? (
										<p className="mt-1 text-xs text-muted-foreground">
											Harness: {harness}
										</p>
									) : null}
									<p
										className="mt-2 truncate font-mono text-[11px] text-muted-foreground"
										title={job.external_run_id ?? job.id}
									>
										Job {job.id}
										{job.external_run_id
											? ` · External ${job.external_run_id}`
											: ""}
									</p>
								</div>
							);
						})
					)}
				</CardContent>
			</Card>
		</div>
	);
}
