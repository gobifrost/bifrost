import { useId, useState } from "react";
import { ChevronDown, ThumbsDown } from "lucide-react";
import { useLocation } from "react-router-dom";

import { getLocationHref } from "@/lib/agent-run-navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentRun } from "@/services/agentRuns";
import type { components } from "@/lib/v1";

import { RunReviewPanel } from "./RunReviewPanel";
import { TONE_MUTED } from "./design-tokens";

type AgentRun = components["schemas"]["AgentRunResponse"];
type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];

export interface FlaggedRunCardProps {
	run: AgentRun;
}

export function FlaggedRunCard({ run }: FlaggedRunCardProps) {
	const location = useLocation();
	const detailsId = useId();
	const [open, setOpen] = useState(false);
	const {
		data: detail,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useAgentRun(open ? run.id : undefined);

	const title = run.asked || run.did || "Run";

	return (
		<div className="min-w-0 overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card">
			<button
				type="button"
				data-testid="flagged-run-toggle"
				onClick={() => setOpen((o) => !o)}
				className="flex min-h-11 w-full items-start gap-3 px-4 py-3 text-left text-sm hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
				aria-expanded={open}
				aria-controls={detailsId}
			>
				<div className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]">
					<ThumbsDown className="h-3 w-3" />
				</div>
				<div className="min-w-0 flex-1">
					<div className="font-medium text-foreground [overflow-wrap:anywhere]">
						{run.asked || run.did ? (
							<MarkdownContent
								content={title}
								variant="preview"
							/>
						) : (
							title
						)}
					</div>
					{run.verdict_note ? (
						<div
							className={cn(
								"mt-1 whitespace-pre-wrap text-sm [overflow-wrap:anywhere]",
								TONE_MUTED,
							)}
							title={run.verdict_note ?? undefined}
						>
							&quot;{run.verdict_note}&quot;
						</div>
					) : null}
				</div>
				<ChevronDown
					className={cn(
						"mt-1 h-3 w-3 shrink-0 transition-transform motion-reduce:transition-none",
						open ? "rotate-0" : "-rotate-90",
					)}
				/>
			</button>
			{open ? (
				<div id={detailsId} className="min-w-0 space-y-3 border-t p-3">
					{isError && (
						<div
							role="alert"
							className="space-y-3 rounded-[var(--bf-radius-control)] bg-[var(--bf-warning-soft)] p-3 text-sm"
						>
							<p>
								Could not {detail ? "refresh" : "load"} the run
								details.
							</p>
							<Button
								variant="outline"
								className="min-h-11"
								disabled={isFetching}
								onClick={() => {
									void refetch();
								}}
							>
								Retry run details
							</Button>
						</div>
					)}
					{isError && !detail ? null : isLoading || !detail ? (
						<Skeleton className="h-24 w-full" />
					) : (
						<RunReviewPanel
							run={detail as AgentRunDetail}
							variant="drawer"
							verdict={null}
							note=""
							onVerdict={() => {}}
							onNote={() => {}}
							hideVerdictBar
							runNavigationOrigin={{
								href: getLocationHref(location),
								label: `Back to ${run.agent_name ?? "agent"} review`,
							}}
						/>
					)}
				</div>
			) : null}
		</div>
	);
}
