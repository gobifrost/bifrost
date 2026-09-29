import { useMemo, useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { formatCost, formatNumber } from "@/lib/utils";
import type { components } from "@/lib/v1";
import { useModelDisplayName } from "@/services/modelNames";

type Usage = components["schemas"]["AIUsagePublicSimple"];
interface ExecutionAiUsageProps {
	usage: Usage[];
	totals?: {
		call_count?: number;
		total_input_tokens: number;
		total_output_tokens: number;
		total_cost?: string | number | null;
	} | null;
}

export function ExecutionAiUsage({ usage, totals }: ExecutionAiUsageProps) {
	const [open, setOpen] = useState(true);
	const modelName = useModelDisplayName();
	const groups = useMemo(() => {
		const result = new Map<
			string,
			{
				provider: string;
				model: string;
				calls: number;
				input: number;
				output: number;
				cost: number;
			}
		>();
		for (const call of usage) {
			const key = JSON.stringify([call.provider, call.model]);
			const group = result.get(key) ?? {
				provider: call.provider,
				model: call.model,
				calls: 0,
				input: 0,
				output: 0,
				cost: 0,
			};
			group.calls++;
			group.input += call.input_tokens;
			group.output += call.output_tokens;
			group.cost += Number(call.cost) || 0;
			result.set(key, group);
		}
		return [...result.entries()];
	}, [usage]);
	const count = totals?.call_count ?? usage.length;
	return (
		<Collapsible open={open} onOpenChange={setOpen} className="min-w-0">
			<CollapsibleTrigger asChild>
				<Button
					variant="ghost"
					className="min-h-11 h-auto w-full justify-start gap-2 px-1 py-2 whitespace-normal"
					aria-label="AI usage details"
				>
					<Sparkles className="size-4 shrink-0 text-primary" />
					<span>AI Usage</span>
					<Badge
						variant="secondary"
						className="h-auto whitespace-normal"
					>
						{count} {count === 1 ? "call" : "calls"}
					</Badge>
					<ChevronDown
						className={`ml-auto size-4 shrink-0 transition-transform duration-[var(--bf-motion-disclosure)] motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
					/>
				</Button>
			</CollapsibleTrigger>
			{totals && (
				<div className="mt-2 border-b border-border pb-3">
					<p className="mb-2 text-sm font-medium text-muted-foreground">
						Total usage
					</p>
					<UsageValues
						input={totals.total_input_tokens}
						output={totals.total_output_tokens}
						cost={totals.total_cost}
					/>
				</div>
			)}
			<CollapsibleContent>
				<ul
					aria-label="AI usage by model"
					className="divide-y divide-border"
				>
					{groups.map(([key, group]) => (
						<li key={key} className="min-w-0 space-y-3 py-3">
							<div className="space-y-1">
								<p className="text-sm font-medium [overflow-wrap:anywhere]" title={group.model}>
									{modelName(group.model)}
								</p>
								<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
									{group.provider} · {group.calls}{" "}
									{group.calls === 1 ? "call" : "calls"}
								</p>
							</div>
							<UsageValues
								input={group.input}
								output={group.output}
								cost={group.cost}
							/>
						</li>
					))}
				</ul>
			</CollapsibleContent>
		</Collapsible>
	);
}

function UsageValues({
	input,
	output,
	cost,
}: {
	input: number;
	output: number;
	cost?: string | number | null;
}) {
	return (
		<dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
			<div className="min-w-0">
				<dt className="text-muted-foreground">Input tokens</dt>
				<dd className="mt-1 font-mono tabular-nums [overflow-wrap:anywhere]">
					{formatNumber(input)}
				</dd>
			</div>
			<div className="min-w-0">
				<dt className="text-muted-foreground">Output tokens</dt>
				<dd className="mt-1 font-mono tabular-nums [overflow-wrap:anywhere]">
					{formatNumber(output)}
				</dd>
			</div>
			<div className="col-span-2 min-w-0">
				<dt className="text-muted-foreground">Cost</dt>
				<dd className="mt-1 font-mono tabular-nums [overflow-wrap:anywhere]">
					{formatCost(cost)}
				</dd>
			</div>
		</dl>
	);
}
