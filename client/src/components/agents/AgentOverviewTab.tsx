/**
 * Overview tab for an agent's detail page.
 *
 * Layout (mirrors /tmp/agent-mockup/src/pages/AgentDetailPage.tsx `OverviewTab`):
 *   main column  →  stat row, activity sparkline card, recent activity list
 *   side column  →  needs-attention card (red), Configuration KV, Budgets KV
 */

import { Link } from "react-router-dom";
import {
	Activity,
	AlertTriangle,
	CheckCircle,
	Clock,
	Info,
	ThumbsDown,
	ThumbsUp,
	XCircle,
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
import { SummaryPlaceholder } from "@/components/agents/SummaryPlaceholder";
import { useAgent } from "@/hooks/useAgents";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import { useAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export interface AgentOverviewTabProps {
	agentId: string;
}

export function AgentOverviewTab({ agentId }: AgentOverviewTabProps) {
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

	return (
		<div className={cn("grid lg:grid-cols-[minmax(0,1fr)_320px]", GAP_CARD)}>
			{/* Main column */}
			<div className={cn("flex flex-col", GAP_CARD)}>
				{/* Stat row — 4 stats */}
				{statsLoading ? (
					<div className={cn("grid grid-cols-2 md:grid-cols-4", GAP_CARD)}>
						{[...Array(4)].map((_, i) => (
							<Skeleton key={i} className="h-[92px] w-full" />
						))}
					</div>
				) : stats ? (
					<div className={cn("grid grid-cols-2 md:grid-cols-4", GAP_CARD)}>
						<StatCard
							label="Runs (7d)"
							value={formatNumber(stats.runs_7d)}
						/>
						<StatCard
							label="Success rate"
							value={`${Math.round(successRate * 100)}%`}
							delta={
								stats.runs_7d > 0 ? `${stats.runs_7d} runs` : "—"
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
						<div className={cn("flex items-center gap-2", TYPE_CARD_TITLE)}>
							<Activity className="h-3.5 w-3.5" /> Activity — last 7
							days
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
				<div className={cn(CARD_SURFACE, "overflow-hidden")}>
					<div
						className={cn(
							"flex items-center justify-between",
							CARD_HEADER,
						)}
					>
						<div className={TYPE_CARD_TITLE}>Recent activity</div>
						<button
							type="button"
							onClick={() => {
								document.querySelector<HTMLElement>(
									'[role="tab"][value="runs"]',
								)?.click();
							}}
							className={cn(
								TYPE_SMALL,
								TONE_MUTED,
								"hover:text-foreground",
							)}
						>
							View all runs →
						</button>
					</div>
					<div>
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
							recentRuns.slice(0, 6).map((r) => (
								<ActivityRow
									key={r.id}
									run={r}
									agentId={agentId}
								/>
							))
						)}
					</div>
				</div>
			</div>

			{/* Side column */}
			<div className={cn("flex flex-col", GAP_CARD)}>
				{needsReview > 0 ? (
					<Link
						to={`/agents/${agentId}/review`}
						className={cn(
							"block overflow-hidden bg-card border-rose-500/40 transition-colors hover:border-rose-500/70",
							CARD_SURFACE,
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
									{unreviewed === 1 ? "" : "s"} awaiting review
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
							"block overflow-hidden transition-colors hover:border-border/80",
							CARD_SURFACE,
						)}
					>
						<div className={CARD_HEADER}>
							<div className={cn("flex items-center gap-2", TYPE_CARD_TITLE)}>
								<Info className="h-3.5 w-3.5" />
								{unreviewed} to review
							</div>
						</div>
						<div className={cn("space-y-2 text-[13px]", CARD_BODY)}>
							<div className="text-muted-foreground">
								Completed runs awaiting a verdict
							</div>
							<div className="mt-1 w-full rounded-md border bg-muted/60 px-3 py-1.5 text-center text-[12.5px]">
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
						<dd className={TYPE_MONO}>{agent?.llm_model ?? "default"}</dd>
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
	agentId,
}: {
	run: AgentRun;
	agentId: string;
}) {
	const status = (run.status ?? "").toLowerCase();
	const iconTone =
		status === "completed"
			? "bg-emerald-500/15 text-emerald-500"
			: status === "failed" || status === "budget_exceeded"
				? "bg-rose-500/15 text-rose-500"
				: "bg-muted text-muted-foreground";
	const Icon =
		status === "completed"
			? CheckCircle
			: status === "running"
				? Clock
				: XCircle;

	return (
		<Link
			to={`/agents/${agentId}/runs/${run.id}`}
			className="flex items-center gap-3 border-b px-4 py-3 text-[13px] last:border-b-0 hover:bg-accent/40"
		>
			<div
				className={cn(
					"grid h-6 w-6 shrink-0 place-items-center rounded-full",
					iconTone,
				)}
			>
				<Icon className="h-3 w-3" />
			</div>
			<div className="min-w-0 flex-1">
				<div className="truncate" title={run.asked ?? undefined}>
					{run.asked || (
						<SummaryPlaceholder
							status={run.summary_status}
							runStatus={run.status}
						/>
					)}
				</div>
				<div
					className="mt-0.5 truncate text-[12px] text-muted-foreground"
					title={run.did ?? undefined}
				>
					{run.did || (
						<SummaryPlaceholder
							status={run.summary_status}
							runStatus={run.status}
							muted
						/>
					)}{" "}
					·{" "}
					{formatRelativeTime(run.started_at ?? run.created_at ?? "")}{" "}
					·{" "}
					{formatDuration(run.duration_ms ?? 0)}
				</div>
			</div>
			{run.verdict === "up" ? (
				<ThumbsUp className="h-3.5 w-3.5 text-emerald-500" />
			) : run.verdict === "down" ? (
				<ThumbsDown className="h-3.5 w-3.5 text-rose-500" />
			) : null}
		</Link>
	);
}

