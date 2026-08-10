/**
 * Overview tab for an agent's detail page.
 *
 * Layout (mirrors /tmp/agent-mockup/src/pages/AgentDetailPage.tsx `OverviewTab`):
 *   main column  →  stat row, activity sparkline card, recent activity list
 *   side column  →  needs-attention card (red), Configuration KV, Budgets KV
 */

import { Link, useLocation } from "react-router-dom";
import {
	Activity,
	AlertTriangle,
	Info,
	ThumbsDown,
	ThumbsUp,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import {
	CARD_BODY,
	CARD_HEADER,
	CARD_SURFACE,
	GAP_CARD,
	TONE_MUTED,
	TYPE_CARD_TITLE,
	TYPE_MONO,
	TYPE_MUTED,
	TYPE_SMALL,
	successRateTone,
} from "@/components/agents/design-tokens";
import { Sparkline } from "@/components/agents/Sparkline";
import { StatCard } from "@/components/agents/StatCard";
import { RunSummaryContent } from "@/components/agents/RunSummaryContent";
import { useAgent } from "@/hooks/useAgents";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import { useAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	createAgentRunNavigationState,
	getLocationHref,
	type AgentRunNavigationOrigin,
} from "@/lib/agent-run-navigation";
import { cn, formatCost, formatDuration, formatNumber } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface AgentOverviewTabProps {
	agentId: string;
}

export function AgentOverviewTab({ agentId }: AgentOverviewTabProps) {
	const location = useLocation();
	const { data: agent } = useAgent(agentId);
	const { data: stats, isLoading: statsLoading } = useAgentStats(agentId);
	const { data: runsList, isLoading: runsLoading } = useAgentRuns({
		agentId,
		limit: 10,
	});

	useAgentRunUpdates({ agentId });
	const recentRuns = (runsList?.items ?? []) as unknown as AgentRun[];
	const needsReview = stats?.needs_review ?? 0;
	const unreviewed = stats?.unreviewed ?? 0;

	const successRate = stats?.success_rate ?? 0;
	const sparkColor = successRateTone(successRate);
	const runNavigationOrigin: AgentRunNavigationOrigin = {
		href: getLocationHref(location),
		label: `Back to ${agent?.name ?? "agent"} overview`,
	};

	return (
		<div
			className={cn(
				"agent-overview-tab grid min-w-0 grid-cols-[minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_320px]",
				GAP_CARD,
			)}
		>
			{/* Main column */}
			<div
				className={cn(
					"agent-overview-main flex min-w-0 flex-col",
					GAP_CARD,
				)}
			>
				{/* Stat row — 4 stats */}
				{statsLoading ? (
					<div
						className={cn(
							"grid grid-cols-2 md:grid-cols-4",
							GAP_CARD,
						)}
					>
						{[...Array(4)].map((_, i) => (
							<Skeleton key={i} className="h-[92px] w-full" />
						))}
					</div>
				) : stats ? (
					<div
						className={cn(
							"grid grid-cols-2 md:grid-cols-4",
							GAP_CARD,
						)}
					>
						<StatCard
							label="Runs (7d)"
							value={formatNumber(stats.runs_7d)}
						/>
						<StatCard
							label="Success rate"
							value={`${Math.round(successRate * 100)}%`}
							delta={
								stats.runs_7d > 0
									? `${stats.runs_7d} runs`
									: "—"
							}
						/>
						<StatCard
							label="Avg duration"
							value={formatDuration(stats.avg_duration_ms)}
						/>
						<StatCard
							label="Spend (7d)"
							value={formatCost(stats.total_cost_7d)}
						/>
					</div>
				) : null}

				{/* Activity — last 7 days */}
				<div className={cn(CARD_SURFACE, "overflow-hidden")}>
					<div
						className={cn(
							"flex items-center justify-between",
							CARD_HEADER,
						)}
					>
						<div
							className={cn(
								"flex items-center gap-2",
								TYPE_CARD_TITLE,
							)}
						>
							<Activity className="h-3.5 w-3.5" /> Activity — last
							7 days
						</div>
						<span className={TYPE_MUTED}>Daily buckets</span>
					</div>
					<div className={cn("h-[140px]", CARD_BODY)}>
						{stats &&
						stats.runs_by_day.length > 1 &&
						stats.runs_by_day.some((v) => v > 0) ? (
							<Sparkline
								values={stats.runs_by_day}
								colorClass={sparkColor}
							/>
						) : (
							<div className="flex h-full items-center justify-center text-sm text-muted-foreground">
								No activity yet
							</div>
						)}
					</div>
				</div>

				{/* Recent activity */}
				<div
					className={cn(
						CARD_SURFACE,
						"agent-overview-recent overflow-hidden",
					)}
				>
					<div
						className={cn(
							"flex items-center justify-between",
							CARD_HEADER,
						)}
					>
						<div className={TYPE_CARD_TITLE}>Recent activity</div>
						<Link
							to={`/agents/${agentId}?tab=runs`}
							className={cn(
								TYPE_SMALL,
								TONE_MUTED,
								"hover:text-foreground",
							)}
						>
							View all runs →
						</Link>
					</div>
					<div
						className="agent-overview-recent-list"
						role="region"
						aria-label="Recent activity"
					>
						{runsLoading ? (
							<div className="space-y-1 p-3">
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
								<Skeleton className="h-12 w-full" />
							</div>
						) : recentRuns.length === 0 ? (
							<p className="py-8 text-center text-[13px] text-muted-foreground">
								No runs yet for this agent.
							</p>
						) : (
							recentRuns
								.slice(0, 6)
								.map((r) => (
									<ActivityRow
										key={r.id}
										run={r}
										runNavigationOrigin={
											runNavigationOrigin
										}
									/>
								))
						)}
					</div>
				</div>
			</div>

			{/* Side column */}
			<div
				className={cn(
					"agent-overview-sidebar flex min-w-0 flex-col",
					GAP_CARD,
				)}
			>
				{needsReview > 0 ? (
					<Link
						to={`/agents/${agentId}/review`}
						className={cn(
							CARD_SURFACE,
							"block overflow-hidden transition-colors ring-rose-500/40 hover:ring-rose-500/70 dark:ring-rose-500/40 dark:hover:ring-rose-500/70",
						)}
					>
						<div className="border-b border-rose-500/20 px-4 py-3">
							<div
								className={cn(
									"flex items-center gap-2 text-rose-500",
									TYPE_CARD_TITLE,
								)}
							>
								<AlertTriangle className="h-3.5 w-3.5" />
								Needs attention
							</div>
						</div>
						<div className={cn("space-y-2 text-[13px]", CARD_BODY)}>
							<div>
								<strong>{needsReview}</strong> run
								{needsReview === 1 ? "" : "s"} marked 👎
							</div>
							{unreviewed > 0 ? (
								<div className="text-muted-foreground">
									{unreviewed} completed run
									{unreviewed === 1 ? "" : "s"} awaiting
									review
								</div>
							) : null}
							<div className="mt-1 w-full rounded-md bg-rose-500/15 px-3 py-1.5 text-center text-[12.5px] font-medium text-rose-500">
								Open review flipbook →
							</div>
						</div>
					</Link>
				) : unreviewed > 0 ? (
					<Link
						to={`/agents/${agentId}/review`}
						className={cn(
							CARD_SURFACE,
							"block overflow-hidden transition-colors hover:ring-foreground/10 dark:hover:ring-foreground/15",
						)}
					>
						<div className={CARD_HEADER}>
							<div
								className={cn(
									"flex items-center gap-2",
									TYPE_CARD_TITLE,
								)}
							>
								<Info className="h-3.5 w-3.5" />
								{unreviewed} to review
							</div>
						</div>
						<div className={cn("space-y-2 text-[13px]", CARD_BODY)}>
							<div className="text-muted-foreground">
								Completed runs awaiting a verdict
							</div>
							<div className="mt-1 w-full rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-1.5 text-center text-[12.5px]">
								Open review flipbook →
							</div>
						</div>
					</Link>
				) : null}

				{/* Configuration */}
				<div className={cn(CARD_SURFACE, "overflow-hidden")}>
					<div className={CARD_HEADER}>
						<div className={TYPE_CARD_TITLE}>Configuration</div>
					</div>
					<dl
						className={cn(
							"grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[13px]",
							CARD_BODY,
						)}
					>
						<dt className={TONE_MUTED}>Model</dt>
						<dd className={TYPE_MONO}>
							{agent?.llm_model ?? "default"}
						</dd>
						<dt className={TONE_MUTED}>Channels</dt>
						<dd>{(agent?.channels ?? []).join(", ") || "—"}</dd>
						<dt className={TONE_MUTED}>Access</dt>
						<dd>
							{agent?.access_level === "authenticated"
								? "Everyone except external users"
								: agent?.access_level === "everyone"
									? "Everyone"
									: "Role-based"}
						</dd>
						<dt className={TONE_MUTED}>Owner</dt>
						<dd className={cn("truncate", TYPE_MONO)}>
							{agent?.created_by ?? "system"}
						</dd>
					</dl>
				</div>

				{/* Budgets */}
				<div className={cn(CARD_SURFACE, "overflow-hidden")}>
					<div className={CARD_HEADER}>
						<div className={TYPE_CARD_TITLE}>Budgets</div>
					</div>
					<dl
						className={cn(
							"grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[13px]",
							CARD_BODY,
						)}
					>
						<dt className={TONE_MUTED}>Max iterations</dt>
						<dd className="tabular-nums">
							{agent?.max_iterations ?? "—"}
						</dd>
						<dt className={TONE_MUTED}>Max tokens</dt>
						<dd className="tabular-nums">
							{agent?.max_token_budget?.toLocaleString() ?? "—"}
						</dd>
					</dl>
				</div>
			</div>
		</div>
	);
}

function ActivityRow({
	run,
	runNavigationOrigin,
}: {
	run: AgentRun;
	runNavigationOrigin: AgentRunNavigationOrigin;
}) {
	return (
		<Link
			to={`/agents/${run.agent_id}/runs/${run.id}`}
			state={createAgentRunNavigationState(runNavigationOrigin)}
			className="flex items-start border-b px-4 py-3 last:border-b-0 hover:bg-accent/40"
		>
			<RunSummaryContent
				run={run}
				density="compact"
				titleTrailing={
					run.verdict === "up" ? (
						<span role="img" aria-label="Verdict: Good">
							<ThumbsUp
								aria-hidden="true"
								className="h-3.5 w-3.5 text-emerald-500"
							/>
						</span>
					) : run.verdict === "down" ? (
						<span role="img" aria-label="Verdict: Wrong">
							<ThumbsDown
								aria-hidden="true"
								className="h-3.5 w-3.5 text-rose-500"
							/>
						</span>
					) : null
				}
			/>
		</Link>
	);
}
