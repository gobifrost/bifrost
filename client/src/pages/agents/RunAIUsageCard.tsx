import { useMemo } from "react";
import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCacheRate, formatContextTokens } from "@/lib/run-usage";
import { formatCost, formatNumber } from "@/lib/utils";
import type { components } from "@/lib/v1";
import { useModelDisplayName } from "@/services/modelNames";

type Run = components["schemas"]["AgentRunDetailResponse"];

export function RunAIUsageCard({
	usage,
	summary,
	reported,
	presentation = "card",
}: {
	usage: NonNullable<Run["ai_usage"]>;
	summary: Run["usage_summary"] | null;
	reported?: { model: string | null; tokens: number };
	presentation?: "card" | "embedded";
}) {
	const modelName = useModelDisplayName();
	const grouped = useMemo(() => {
		const rows = new Map<
			string,
			{
				model: string;
				calls: number;
				input: number;
				cacheRead: number;
				output: number;
				reasoning: number;
				cost: number;
			}
		>();
		for (const entry of usage) {
			const row = rows.get(entry.model) ?? {
				model: entry.model,
				calls: 0,
				input: 0,
				cacheRead: 0,
				output: 0,
				reasoning: 0,
				cost: 0,
			};
			row.calls++;
			row.input += entry.input_tokens;
			row.cacheRead += entry.cache_read_tokens ?? 0;
			row.output += entry.output_tokens;
			row.reasoning += entry.reasoning_tokens ?? 0;
			row.cost += Number(entry.cost) || 0;
			rows.set(entry.model, row);
		}
		return [...rows.values()];
	}, [usage]);
	const delegateCost = Number(summary?.delegate_cost) || 0;
	const content = (
		<>
			<CardHeader className="pb-2">
				<CardTitle className="flex items-center gap-2 text-sm">
					<Sparkles className="h-4 w-4 text-primary" />
					AI Usage
				</CardTitle>
			</CardHeader>
			<CardContent className="min-w-0 space-y-4">
				{summary ? (
					<div className="space-y-2">
						<dl className="grid grid-cols-3 gap-x-4 text-xs">
							<HeadlineMetric
								label="Cost"
								value={summary.cost != null ? formatCost(summary.cost) : "—"}
								help="Everything this run spent, including delegated agents and the run summary."
							/>
							<HeadlineMetric
								label="Peak context"
								value={
									summary.peak_context_tokens != null
										? `${formatContextTokens(summary.peak_context_tokens)} tokens`
										: "—"
								}
								help="The largest single request this run sent to the model."
							/>
							<HeadlineMetric
								label="Cached"
								value={
									summary.cache_hit_rate != null
										? formatCacheRate(summary.cache_hit_rate)
										: "—"
								}
								help="Share of input read from the provider's prompt cache, billed at a reduced rate."
							/>
						</dl>
						{delegateCost > 0 ? (
							<p className="text-xs text-muted-foreground">
								Includes {formatCost(delegateCost)} from delegated
								agents.
							</p>
						) : null}
					</div>
				) : null}
				{grouped.length === 0 && reported ? (
					<dl className="space-y-3 text-xs">
						{reported.model ? (
							<div>
								<dt className="text-muted-foreground">Model</dt>
								<dd className="mt-1 font-mono [overflow-wrap:anywhere]">
									{modelName(reported.model)}
								</dd>
							</div>
						) : null}
						<div>
							<dt className="text-muted-foreground">Tokens</dt>
							<dd className="mt-1 tabular-nums">
								{formatNumber(reported.tokens)}
							</dd>
						</div>
					</dl>
				) : null}
				{grouped.length > 0 ? (
					<div className="space-y-1 border-t pt-3">
						<p className="text-xs font-medium">This run&apos;s calls</p>
						<ul className="divide-y">
							{grouped.map((row) => (
								<li
									key={row.model}
									className="min-w-0 space-y-3 py-3 first:pt-1"
								>
									<p className="text-xs font-medium [overflow-wrap:anywhere]" title={row.model}>
										{modelName(row.model)}
									</p>
									<UsageMetrics
										calls={row.calls}
										input={row.input}
										cacheRead={row.cacheRead}
										output={row.output}
										reasoning={row.reasoning}
										cost={row.cost}
									/>
								</li>
							))}
						</ul>
					</div>
				) : null}
			</CardContent>
		</>
	);
	if (presentation === "embedded") {
		return (
			<div
				data-testid="ai-usage-card"
				className="[&_[data-slot=card-content]]:px-0 [&_[data-slot=card-header]]:px-0"
			>
				{content}
			</div>
		);
	}
	return <Card data-testid="ai-usage-card">{content}</Card>;
}

function HeadlineMetric({
	label,
	value,
	help,
}: {
	label: string;
	value: string;
	help: string;
}) {
	return (
		<div className="min-w-0" title={help}>
			<dt className="text-muted-foreground">{label}</dt>
			<dd className="mt-1 text-base font-semibold tabular-nums">{value}</dd>
		</div>
	);
}

function UsageMetrics({
	calls,
	input,
	cacheRead,
	output,
	reasoning,
	cost,
}: {
	calls: number;
	input: number;
	cacheRead: number;
	output: number;
	/** Hidden reasoning tokens, already counted in output. */
	reasoning: number;
	cost: number;
}) {
	return (
		<dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs sm:grid-cols-4">
			{[
				["Calls", formatNumber(calls)],
				["Cost", formatCost(cost)],
				["Cached", input > 0 ? formatCacheRate(cacheRead / input) : "—"],
				[
					"Output tokens",
					reasoning > 0
						? `${formatNumber(output)} (${formatNumber(reasoning)} reasoning)`
						: formatNumber(output),
				],
			].map(([label, value]) => (
				<div key={label} className="min-w-0">
					<dt className="text-muted-foreground">{label}</dt>
					<dd className="mt-1 font-mono tabular-nums [overflow-wrap:anywhere]">
						{value}
					</dd>
				</div>
			))}
		</dl>
	);
}
