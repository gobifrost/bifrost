import { useState } from "react";
import { ChevronDown, ThumbsDown } from "lucide-react";
import { useLocation } from "react-router-dom";

import { getLocationHref } from "@/lib/agent-run-navigation";
import { cn } from "@/lib/utils";
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
	const [open, setOpen] = useState(false);
	const { data: detail, isLoading } = useAgentRun(
		open ? run.id : undefined,
	);

	const title = run.asked || run.did || "Run";

	return (
		<div className="overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10">
			<button
				type="button"
				data-testid="flagged-run-toggle"
				onClick={() => setOpen((o) => !o)}
				className="flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:bg-accent/40"
				aria-expanded={open}
			>
				<div className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-rose-500/15 text-rose-500">
					<ThumbsDown className="h-3 w-3" />
				</div>
				<div className="min-w-0 flex-1">
					<div className="truncate text-foreground">{title}</div>
					{run.verdict_note ? (
						<div
							className={cn(
								"truncate text-[11px] italic",
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
						"mt-1 h-3 w-3 shrink-0 transition-transform",
						open ? "rotate-0" : "-rotate-90",
					)}
				/>
			</button>
			{open ? (
				<div className="border-t p-2">
					{isLoading || !detail ? (
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
								label: `Back to ${run.agent_name ?? "agent"} tuning`,
							}}
						/>
					)}
				</div>
			) : null}
		</div>
	);
}
