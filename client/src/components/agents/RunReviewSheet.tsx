/**
 * Slide-over sheet that wraps a run's review and activity.
 *
 * Mounts the shared RunReviewPanel with the run activity below it. The parent
 * controls open state, the run, and all state for verdict / note; this
 * component remains purely presentational.
 */

import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { useState, type ReactNode } from "react";
import { ExternalLink, ListTree, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetDescription,
} from "@/components/ui/sheet";
import {
	createAgentRunNavigationState,
	getLocationHref,
} from "@/lib/agent-run-navigation";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

import { RunReviewPanel, type Verdict } from "./RunReviewPanel";
import { Timeline } from "./Timeline";
import { activityDomId } from "./run-activity";

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];

export interface RunReviewSheetProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	run: AgentRunDetail | null;
	verdict: Verdict;
	note: string;
	onVerdict: (v: Verdict) => void;
	onNote: (n: string) => void;
	reviewPending?: boolean;
	reviewFeedback?: ReactNode;
	reviewActions?: ReactNode;
	readFeedback?: ReactNode;
}

export function RunReviewSheet({
	open,
	onOpenChange,
	run,
	verdict,
	note,
	onVerdict,
	onNote,
	reviewPending,
	reviewFeedback,
	reviewActions,
	readFeedback,
}: RunReviewSheetProps) {
	const location = useLocation();
	const dialogFocus = useDialogReturnFocus();
	const [activityPreview, setActivityPreview] = useState<{
		runId: string;
		activityId: string;
	} | null>(null);

	if (!run)
		return (
			<Sheet open={open} onOpenChange={onOpenChange}>
				<SheetContent
					{...dialogFocus}
					side="right"
					aria-label="Run review"
					className="w-full bg-background sm:max-w-2xl"
				>
					<SheetHeader>
						<SheetTitle>Run review</SheetTitle>
						<SheetDescription>
							Review this run’s activity and outcome.
						</SheetDescription>
					</SheetHeader>
					<div className="px-4 pb-4 sm:px-6">
						{readFeedback || (
							<p
								role="status"
								className="text-sm text-muted-foreground"
							>
								Loading run details…
							</p>
						)}
					</div>
				</SheetContent>
			</Sheet>
		);
	const runId = run.id;
	const runNavigationOrigin = {
		href: getLocationHref(location),
		label: `Back to ${run.agent_name ?? "agent"} runs`,
	};

	const highlightedActivityId =
		activityPreview?.runId === runId ? activityPreview.activityId : null;

	function handleActivityReferencePreview(activityId: string | null) {
		setActivityPreview(activityId ? { runId, activityId } : null);
	}

	function handleActivityReferenceActivate(activityId: string) {
		const target = document.getElementById(activityDomId(activityId));
		if (!target) return;
		const reduceMotion =
			window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ??
			false;
		target.scrollIntoView({
			behavior: reduceMotion ? "auto" : "smooth",
			block: "center",
		});
		target.focus({ preventScroll: true });
	}

	function handleOpenChange(nextOpen: boolean) {
		if (!nextOpen) setActivityPreview(null);
		onOpenChange(nextOpen);
	}

	return (
		<Sheet open={open} onOpenChange={handleOpenChange}>
			<SheetContent
				{...dialogFocus}
				side="right"
				aria-label="Run review"
				showCloseButton={false}
				className={cn(
					"agent-run-review-sheet flex h-full w-full max-w-none flex-col gap-0 overflow-hidden border-border/70 bg-background p-0 text-foreground shadow-xl motion-reduce:transition-none motion-reduce:animate-none sm:max-w-2xl",
				)}
			>
				<RunReviewSheetHeader
					title={run.asked || run.did || "Run review"}
					runId={run.id}
					agentId={run.agent_id}
					runNavigationOrigin={runNavigationOrigin}
					onClose={() => handleOpenChange(false)}
					closeDisabled={reviewPending}
				/>
				{readFeedback ? (
					<div className="shrink-0 px-4 pt-4 sm:px-6">
						{readFeedback}
					</div>
				) : null}
				<RunReviewSheetScrollRegion>
					{reviewFeedback ? (
						<div className="px-4 pt-4 sm:px-6">
							{reviewFeedback}
						</div>
					) : null}
					<fieldset
						disabled={reviewPending}
						className="min-w-0 space-y-3"
					>
						<RunReviewPanel
							run={run}
							verdict={verdict}
							note={note}
							onVerdict={onVerdict}
							onNote={onNote}
							variant="drawer"
							runNavigationOrigin={runNavigationOrigin}
							onActivityReferencePreview={
								handleActivityReferencePreview
							}
							onActivityReferenceActivate={
								handleActivityReferenceActivate
							}
						/>
						{reviewActions ? (
							<div className="px-4 pb-4 sm:px-6">
								{reviewActions}
							</div>
						) : null}
					</fieldset>
					<RunReviewSheetActivitySection
						highlightedActivityId={highlightedActivityId}
						run={run}
						runNavigationOrigin={runNavigationOrigin}
					/>
				</RunReviewSheetScrollRegion>
			</SheetContent>
		</Sheet>
	);
}

function RunReviewSheetHeader({
	closeDisabled,
	title,
	runId,
	agentId,
	runNavigationOrigin,
	onClose,
}: {
	title: string;
	runId: string;
	agentId: string | null;
	runNavigationOrigin: {
		href: string;
		label: string;
	};
	onClose: () => void;
	closeDisabled?: boolean;
}) {
	return (
		<SheetHeader className="shrink-0 border-b border-border/80 bg-background/95 px-4 pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-left backdrop-blur supports-[backdrop-filter]:bg-background/90 sm:px-6">
			<div className="flex min-w-0 flex-col gap-3">
				<div className="flex items-start gap-3">
					<div className="min-w-0 flex-1">
						<div className="mb-1 inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">
							<span className="h-2 w-2 rounded-full bg-primary" />
							Run review
						</div>
						<SheetTitle className="text-pretty break-words text-lg leading-6 sm:text-xl">
							<MarkdownContent
								content={title}
								variant="preview"
							/>
						</SheetTitle>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						onClick={onClose}
						disabled={closeDisabled}
						aria-label="Close run review"
						className="h-11 w-11 shrink-0 border border-border/70 bg-background/90 text-foreground hover:bg-muted motion-reduce:transition-none"
					>
						<X className="h-5 w-5" />
					</Button>
				</div>
				<div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
					<p className="max-w-2xl text-sm leading-5 text-muted-foreground">
						Review verdicts, notes, and activity for this run.
					</p>
					<Button
						asChild
						variant="outline"
						className="h-11 w-full shrink-0 justify-between gap-2 px-3 text-[13px] font-medium text-primary hover:bg-accent sm:w-auto"
					>
						<Link
							to={`/agents/${agentId}/runs/${runId}`}
							state={createAgentRunNavigationState(
								runNavigationOrigin,
							)}
							aria-label="Open full run page"
						>
							Open full run
							<ExternalLink className="h-4 w-4" />
						</Link>
					</Button>
				</div>
			</div>
		</SheetHeader>
	);
}

function RunReviewSheetScrollRegion({ children }: { children: ReactNode }) {
	return (
		<div className="agent-run-review-content flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain bg-background">
			{children}
		</div>
	);
}

function RunReviewSheetActivitySection({
	run,
	runNavigationOrigin,
	highlightedActivityId,
}: {
	run: AgentRunDetail;
	runNavigationOrigin: {
		href: string;
		label: string;
	};
	highlightedActivityId: string | null;
}) {
	return (
		<section
			className="border-t border-border/70 px-4 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-4 sm:px-6"
			data-slot="run-activity"
			aria-labelledby="run-review-sheet-activity-title"
		>
			<div className="mb-4">
				<h3
					id="run-review-sheet-activity-title"
					className="flex items-center gap-2 text-sm font-semibold"
				>
					<ListTree className="h-4 w-4 text-primary" />
					Activity
				</h3>
				<p className="mt-1 text-xs leading-5 text-muted-foreground">
					How the agent handled this run, in order
				</p>
			</div>
			<Timeline
				steps={run.steps ?? []}
				childRunIds={run.child_run_ids ?? []}
				childRuns={run.child_runs ?? []}
				runStatus={run.status}
				highlightedActivityId={highlightedActivityId}
				childRunOrigin={runNavigationOrigin}
			/>
		</section>
	);
}
