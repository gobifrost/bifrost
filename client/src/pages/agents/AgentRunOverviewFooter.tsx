import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Bot, ChevronDown, Info, Sparkles } from "lucide-react";

import { SummaryRegenerationControl } from "@/components/agents/SummaryRegenerationControl";
import { Button } from "@/components/ui/button";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { cn, formatCost, formatDuration, formatNumber } from "@/lib/utils";
import type { components } from "@/lib/v1";

import { RunAIUsageCard } from "./RunAIUsageCard";

type Run = components["schemas"]["AgentRunDetailResponse"];
type Disclosure = "usage" | "metadata" | null;

export function AgentRunOverviewFooter({
	run,
	agentName,
	showRegen,
	isPlatformAdmin,
}: {
	run: Run;
	agentName: string | null;
	showRegen: boolean;
	isPlatformAdmin: boolean;
}) {
	const [active, setActive] = useState<Disclosure>(null);
	const hasUsage =
		(run.ai_usage?.length ?? 0) > 0 ||
		!!run.llm_model ||
		run.tokens_used > 0;
	return (
		<div className="border-t bg-muted/25" data-testid="run-overview-footer">
			<div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
				<div className="flex min-w-0 flex-wrap items-center gap-2">
					{hasUsage ? (
						<FooterToggle
							active={active === "usage"}
							icon={<Sparkles className="size-4" />}
							label="AI Usage"
							summary={usageSummary(run)}
							onClick={() =>
								setActive(active === "usage" ? null : "usage")
							}
						/>
					) : null}
					<FooterToggle
						active={active === "metadata"}
						icon={<Info className="size-4" />}
						label="Run metadata"
						summary={metadataSummary(run)}
						onClick={() =>
							setActive(active === "metadata" ? null : "metadata")
						}
					/>
				</div>
				{showRegen ? (
					<SummaryRegenerationControl
						runId={run.id}
						allowed={isPlatformAdmin}
						testId="regen-summary-button"
					/>
				) : !run.agent_id ? (
					<p className="text-xs text-muted-foreground">
						This agent is no longer available.
					</p>
				) : null}
			</div>
			{active ? (
				<div className="border-t px-4 py-4">
					{active === "usage" ? (
						<RunAIUsageCard
							usage={run.ai_usage ?? []}
							reported={{
								model: run.llm_model ?? null,
								tokens: run.tokens_used,
							}}
							totals={run.ai_totals ?? null}
							presentation="embedded"
						/>
					) : (
						<MetadataDetail
							run={run}
							agentName={agentName}
							showRegen={showRegen}
							isPlatformAdmin={isPlatformAdmin}
						/>
					)}
				</div>
			) : null}
		</div>
	);
}

function FooterToggle({
	active,
	icon,
	label,
	summary,
	onClick,
}: {
	active: boolean;
	icon: ReactNode;
	label: string;
	summary?: string;
	onClick: () => void;
}) {
	return (
		<Button
			type="button"
			variant="ghost"
			aria-expanded={active}
			onClick={onClick}
			className={cn(
				"min-h-11 min-w-0 gap-2 px-2 text-xs text-muted-foreground hover:text-foreground",
				active && "text-primary",
			)}
		>
			{icon}
			<span className="font-medium text-foreground">{label}</span>
			{summary ? (
				<span className="min-w-0 truncate">{summary}</span>
			) : null}
			<ChevronDown
				className={cn(
					"size-3.5 transition-transform duration-[var(--bf-motion-disclosure)] motion-reduce:transition-none",
					active && "rotate-180",
				)}
			/>
		</Button>
	);
}

function usageSummary(run: Run): string {
	const runtime = run.ai_usage?.filter((entry) => entry.sequence !== 0);
	const hasSummaries = run.ai_usage?.some((entry) => entry.sequence === 0);
	const tokens = hasSummaries
		? runtime!.reduce(
				(sum, entry) => sum + entry.input_tokens + entry.output_tokens,
				0,
			)
		: run.ai_totals
			? run.ai_totals.total_input_tokens +
				run.ai_totals.total_output_tokens
			: run.tokens_used;
	const cost = hasSummaries
		? String(
				runtime!.reduce(
					(sum, entry) => sum + Number(entry.cost ?? 0),
					0,
				),
			)
		: run.ai_totals?.total_cost;
	return [
		tokens > 0 ? `${formatNumber(tokens)} tokens` : null,
		cost != null ? formatCost(cost) : null,
	]
		.filter(Boolean)
		.join(" · ");
}

function metadataSummary(run: Run): string {
	return [
		run.trigger_type ? `Trigger ${run.trigger_type}` : null,
		run.iterations_used != null
			? `${formatNumber(run.iterations_used)} iterations`
			: null,
	]
		.filter(Boolean)
		.join(" · ");
}

function MetadataDetail({
	run,
	agentName,
	showRegen,
	isPlatformAdmin,
}: {
	run: Run;
	agentName: string | null;
	showRegen: boolean;
	isPlatformAdmin: boolean;
}) {
	const metadata = run.metadata ?? {};
	return (
		<div className="space-y-5 text-xs">
			<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
				<Metric label="Trigger" value={run.trigger_type || "Unknown"} />
				<Metric
					label="Iterations"
					value={formatNumber(run.iterations_used ?? 0)}
				/>
				{run.duration_ms != null ? (
					<Metric
						label="Duration"
						value={formatDuration(run.duration_ms)}
					/>
				) : null}
				{run.agent_id ? (
					<div className="min-w-0">
						<div className="text-muted-foreground">Agent</div>
						<Link
							to={`/agents/${run.agent_id}`}
							className="mt-1 inline-flex min-h-11 max-w-full items-center gap-2 rounded-[var(--bf-radius-control)] text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<Bot className="size-4 shrink-0 text-muted-foreground" />
							<span className="truncate">
								{agentName ?? run.agent_name ?? "Agent"}
							</span>
						</Link>
					</div>
				) : (
					<Metric
						label="Agent"
						value={run.agent_name ?? "Deleted agent"}
					/>
				)}
				{showRegen ? (
					<div className="min-w-0">
						<div className="text-muted-foreground">Summary</div>
						<div className="mt-1 flex min-h-11 items-center">
							<SummaryRegenerationControl
								runId={run.id}
								allowed={isPlatformAdmin}
								testId="regen-summary-metadata-button"
							/>
						</div>
					</div>
				) : null}
			</div>
			{Object.keys(metadata).length ? (
				<div className="rounded-[var(--bf-radius-surface)] border bg-card p-3">
					<div className="mb-2 text-muted-foreground">
						Captured data
					</div>
					<VariablesTreeView data={metadata} />
				</div>
			) : null}
		</div>
	);
}

function Metric({ label, value }: { label: string; value: string }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground">{label}</dt>
			<dd className="mt-1 break-words text-foreground">{value}</dd>
		</div>
	);
}
