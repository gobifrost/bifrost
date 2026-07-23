/**
 * Slide-over sheet that wraps a run's review and tuning experience.
 *
 * Mounts the shared RunReviewPanel under the Review tab and the
 * FlagConversation under the Tune tab. The parent controls open state,
 * the run, and all state for verdict / note / conversation — this
 * component is purely presentational.
 */

import { useState } from "react";
import { ExternalLink, ListTree, Sparkles } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	createAgentRunNavigationState,
	getLocationHref,
} from "@/lib/agent-run-navigation";
import type { components } from "@/lib/v1";

import { FlagConversation } from "./FlagConversation";
import { RunReviewPanel, type Verdict } from "./RunReviewPanel";
import { Timeline } from "./Timeline";
import { activityDomId } from "./run-activity";

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];
type FlagConversationResponse =
	components["schemas"]["FlagConversationResponse"];

export interface RunReviewSheetProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	run: AgentRunDetail | null;
	verdict: Verdict;
	note: string;
	onVerdict: (v: Verdict) => void;
	onNote: (n: string) => void;
	conversation: FlagConversationResponse | null;
	onSendChat: (text: string) => void;
	chatPending?: boolean;
	onTestAgainstRun?: () => void;
	defaultTab?: "review" | "tune";
}

export function RunReviewSheet({
	open,
	onOpenChange,
	run,
	verdict,
	note,
	onVerdict,
	onNote,
	conversation,
	onSendChat,
	chatPending,
	onTestAgainstRun,
	defaultTab = "review",
}: RunReviewSheetProps) {
	const location = useLocation();
	const [activityPreview, setActivityPreview] = useState<{
		runId: string;
		activityId: string;
	} | null>(null);

	if (!run) return null;
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
				side="right"
				aria-label="Run review"
				className="flex w-full flex-col gap-0 p-0 sm:max-w-2xl"
			>
				{/* pr-12 leaves room for the absolutely-positioned X close
				    button (top-4 right-4 in ui/sheet.tsx); without this, the
				    "Open full run" link overlaps the X. */}
				<SheetHeader className="border-b py-4 pl-6 pr-12">
					<div className="flex items-center justify-between gap-3">
						<SheetTitle className="truncate">
							{/* `asked` is the bounded TL;DR; `did` is
							    multi-sentence prose under v3+ and too long
							    for a title. */}
							{run.asked || run.did || "Run review"}
						</SheetTitle>
						<Link
							to={`/agents/${run.agent_id}/runs/${run.id}`}
							state={createAgentRunNavigationState(
								runNavigationOrigin,
							)}
							className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
							aria-label="Open full run page"
						>
							Open full run
							<ExternalLink className="h-3 w-3" />
						</Link>
					</div>
				</SheetHeader>
				<Tabs
					defaultValue={defaultTab}
					className="flex min-h-0 flex-1 flex-col gap-0"
				>
					<TabsList className="mx-6 mt-3 self-start">
						<TabsTrigger value="review">Review</TabsTrigger>
						<TabsTrigger value="tune">
							<Sparkles size={12} /> Tune
						</TabsTrigger>
					</TabsList>
					<TabsContent
						value="review"
						className="flex-1 overflow-y-auto"
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
						<section
							className="border-t px-4 pb-5 pt-4"
							data-slot="run-activity"
							aria-labelledby="run-review-sheet-activity-title"
						>
							<div className="mb-4">
								<h3
									id="run-review-sheet-activity-title"
									className="flex items-center gap-2 text-sm font-semibold"
								>
									<ListTree className="h-4 w-4 text-muted-foreground" />
									Activity
								</h3>
								<p className="mt-1 text-xs text-muted-foreground">
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
					</TabsContent>
					<TabsContent
						value="tune"
						className="flex min-h-0 flex-1 flex-col"
					>
						<FlagConversation
							conversation={conversation}
							onSend={onSendChat}
							pending={chatPending}
							onTestAgainstRun={onTestAgainstRun}
						/>
					</TabsContent>
				</Tabs>
			</SheetContent>
		</Sheet>
	);
}
