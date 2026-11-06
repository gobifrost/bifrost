import {
	Activity,
	CheckCircle2,
	CircleStop,
	Clock3,
	Loader2,
	PauseCircle,
	TriangleAlert,
} from "lucide-react";

import { Progress } from "@/components/ui/progress";
import type { PlatformJob } from "@/services/platformJobs";

export type ConversionJobKind = "analysis" | "apply" | "cutover";

const labels: Record<
	ConversionJobKind,
	{ noun: string; queued: string; running: string; completed: string }
> = {
	analysis: {
		noun: "analysis",
		queued: "Analysis queued",
		running: "Analysis is running",
		completed: "Analysis completed",
	},
	apply: {
		noun: "changes",
		queued: "Changes queued",
		running: "Requested changes are running",
		completed: "Requested changes completed",
	},
	cutover: {
		noun: "platform cutover",
		queued: "Platform cutover queued",
		running: "Platform cutover is running",
		completed: "Platform cutover completed",
	},
};

function isGenericPhase(phase: string | null | undefined, status: string) {
	return !phase || phase.trim().toLowerCase() === status.replaceAll("_", " ");
}

/** Compact, truthful durable-job status for the conversion wizard. */
export function ConversionJobProgress({
	job,
	kind,
	titleId,
}: {
	job: PlatformJob | null;
	kind: ConversionJobKind;
	titleId: string;
}) {
	const label = labels[kind];
	const status = job?.status;
	const phase = job?.progress.phase;
	const hasMeasuredProgress =
		status === "running" &&
		(job?.progress.total ?? 0) > 0 &&
		job?.progress.percent != null;
	const count = hasMeasuredProgress
		? `${job.progress.current} of ${job.progress.total} completed`
		: null;

	let icon = (
		<Loader2 className="size-6 animate-spin motion-reduce:animate-none" />
	);
	let heading = "Restoring request status";
	let copy = "Checking the saved request status.";

	if (status === "queued") {
		icon = <Clock3 className="size-6" />;
		heading = label.queued;
		copy =
			kind === "analysis"
				? "Your request is saved. Analysis starts when a runner is available."
				: kind === "apply"
					? "Your request is saved. Changes start when a runner is available."
					: "Your request is saved. The platform cutover starts when a runner is available.";
	} else if (status === "waiting") {
		icon = <PauseCircle className="size-6" />;
		heading = `${label.noun[0].toUpperCase()}${label.noun.slice(1)} is waiting`;
		copy = isGenericPhase(phase, status)
			? "This request is paused pending the next required condition."
			: phase!;
	} else if (status === "running") {
		icon = hasMeasuredProgress ? (
			<Activity className="size-6" />
		) : (
			<Loader2 className="size-6 animate-spin motion-reduce:animate-none" />
		);
		heading = label.running;
		copy = isGenericPhase(phase, status) ? "Work is underway." : phase!;
	} else if (status === "cancel_requested") {
		icon = <CircleStop className="size-6" />;
		heading = `Stopping ${kind === "analysis" ? "analysis" : kind === "apply" ? "requested changes" : "platform cutover"}`;
		copy = "The runner is stopping this saved request.";
	} else if (status === "succeeded") {
		icon = <CheckCircle2 className="size-6" />;
		heading = label.completed;
		copy = "The saved request finished. Review the outcome below.";
	} else if (status === "cancelled") {
		icon = <CircleStop className="size-6" />;
		heading = `${label.noun[0].toUpperCase()}${label.noun.slice(1)} stopped`;
		copy =
			"The saved request was cancelled. Review any recorded outcome below.";
	} else if (status === "failed" || status === "requires_action") {
		icon = <TriangleAlert className="size-6" />;
		heading = `${label.noun[0].toUpperCase()}${label.noun.slice(1)} needs attention`;
		copy = "The saved request needs review before it can continue.";
	}

	return (
		<div className="mx-auto flex w-full max-w-xl flex-col items-center px-4 py-10 text-center sm:py-14">
			<div className="flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
				{icon}
			</div>
			<h2 id={titleId} className="mt-4 text-lg font-semibold">
				{heading}
			</h2>
			<p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
				{copy}
			</p>
			{hasMeasuredProgress && (
				<div className="mt-6 w-full max-w-md space-y-2">
					<Progress
						aria-label={`${label.noun} progress`}
						aria-valuetext={count ?? undefined}
						value={job.progress.percent ?? undefined}
					/>
					<p className="text-sm text-muted-foreground">{count}</p>
				</div>
			)}
			{kind === "analysis" &&
				(status === "queued" ||
					status === "waiting" ||
					status === "running") && (
					<p className="mt-5 text-sm text-muted-foreground">
						Analysis is read-only and will not change workflow
						access.
					</p>
				)}
		</div>
	);
}
