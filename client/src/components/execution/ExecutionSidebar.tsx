import { useState, useMemo, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Info, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PrettyInputDisplay } from "./PrettyInputDisplay";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import {
	formatDate,
	formatRelativeTime,
	formatBytes,
	formatNumber,
	formatCost,
	formatDuration,
} from "@/lib/utils";
import type { components } from "@/lib/v1";

type AIUsagePublicSimple = components["schemas"]["AIUsagePublicSimple"];

interface ExecutionSidebarProps {
	/** Who executed the workflow */
	executedByName?: string | null;
	/** Organization name (effective scope) */
	orgName?: string | null;
	/** Scheduled run timestamp (deferred executions only) */
	scheduledAt?: string | null;
	/** Start timestamp */
	startedAt?: string | null;
	/** Completion timestamp */
	completedAt?: string | null;
	/** Input parameters passed to the workflow */
	inputData?: unknown;
	/** Whether the execution is complete */
	isComplete: boolean;
	/** Whether the current user is a platform admin */
	isPlatformAdmin: boolean;
	/** Whether data is still loading */
	isLoading: boolean;
	/** Runtime variables (admin only) */
	variablesData?: Record<string, unknown>;
	/** Peak memory usage in bytes */
	peakMemoryBytes?: number | null;
	/** CPU time in seconds */
	cpuTotalSeconds?: number | null;
	/** Duration in milliseconds */
	durationMs?: number | null;
	/** AI usage data */
	aiUsage?: AIUsagePublicSimple[] | null;
	/** AI usage totals */
	aiTotals?: {
		call_count?: number;
		total_input_tokens: number;
		total_output_tokens: number;
		total_cost?: string | number | null;
		total_duration_ms?: number | null;
	} | null;
	/** Persisted execution context (admin only) */
	executionContext?: Record<string, unknown> | null;
	/** When true, only render AI usage, metrics, variables, and execution context — skip details and input sections */
	extrasOnly?: boolean;
}

/**
 * Inspector section idiom shared with the result/logs panels: a compact
 * small-caps header (with optional muted description and trailing action),
 * then content that carries a single step-1 surface.
 */
function InspectorSection({
	title,
	description,
	action,
	children,
}: {
	title: string;
	description?: string;
	action?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section>
			<div className="mb-1.5 flex items-start justify-between gap-2">
				<div className="min-w-0">
					<h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
						{title}
					</h4>
					{description && (
						<p className="mt-0.5 text-xs text-muted-foreground/80">
							{description}
						</p>
					)}
				</div>
				{action}
			</div>
			{children}
		</section>
	);
}

export function ExecutionSidebar({
	executedByName,
	orgName,
	scheduledAt,
	startedAt,
	completedAt,
	inputData,
	isComplete,
	isPlatformAdmin,
	isLoading,
	variablesData,
	peakMemoryBytes,
	cpuTotalSeconds,
	durationMs,
	aiUsage,
	aiTotals,
	executionContext,
	extrasOnly = false,
}: ExecutionSidebarProps) {
	const [isAiUsageOpen, setIsAiUsageOpen] = useState(true);

	const groupedAiUsage = useMemo(() => {
		if (!aiUsage?.length) return [];
		const groups = new Map<string, { provider: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost: number }>();
		for (const usage of aiUsage) {
			const existing = groups.get(usage.model);
			const costNum = usage.cost ? parseFloat(String(usage.cost)) || 0 : 0;
			if (existing) {
				existing.calls += 1;
				existing.input_tokens += usage.input_tokens;
				existing.output_tokens += usage.output_tokens;
				existing.cost += costNum;
			} else {
				groups.set(usage.model, {
					provider: usage.provider,
					model: usage.model,
					calls: 1,
					input_tokens: usage.input_tokens,
					output_tokens: usage.output_tokens,
					cost: costNum,
				});
			}
		}
		return Array.from(groups.values());
	}, [aiUsage]);

	return (
		<div className="space-y-5">
			{!extrasOnly && (
				<>
					{/* Details — dense definition list: one step-1 surface,
					    hairline-separated rows. The workflow name and status
					    live in the page header; repeating them here was pure
					    duplication. */}
					<InspectorSection title="Details">
						<dl className="divide-y divide-border/60 overflow-hidden rounded-lg bg-muted/50 ring-1 ring-foreground/5 text-sm">
							<div className="flex items-baseline justify-between gap-4 px-3 py-2">
								<dt className="text-muted-foreground">
									Run by
								</dt>
								<dd className="text-right">
									{executedByName || "Unknown"}
								</dd>
							</div>
							<div className="flex items-baseline justify-between gap-4 px-3 py-2">
								<dt className="text-muted-foreground">
									Scope
								</dt>
								<dd className="text-right">
									{orgName || "Global"}
								</dd>
							</div>
							{scheduledAt && (
								<div className="flex items-baseline justify-between gap-4 px-3 py-2">
									<dt className="text-muted-foreground">
										Scheduled for
									</dt>
									<dd className="text-right">
										{formatDate(scheduledAt)}
									</dd>
								</div>
							)}
							<div className="flex items-baseline justify-between gap-4 px-3 py-2">
								<dt className="text-muted-foreground">
									Started
								</dt>
								<dd
									className="text-right"
									{...(startedAt
										? { title: formatDate(startedAt) }
										: {})}
								>
									{startedAt
										? formatRelativeTime(startedAt)
										: "Not started"}
								</dd>
							</div>
							{completedAt && (
								<div className="flex items-baseline justify-between gap-4 px-3 py-2">
									<dt className="text-muted-foreground">
										Completed
									</dt>
									<dd
										className="text-right"
										title={formatDate(completedAt)}
									>
										{formatRelativeTime(completedAt)}
									</dd>
								</div>
							)}
							{durationMs != null && (
								<div className="flex items-baseline justify-between gap-4 px-3 py-2">
									<dt className="text-muted-foreground">
										Duration
									</dt>
									<dd className="text-right font-mono tabular-nums">
										{formatDuration(durationMs)}
									</dd>
								</div>
							)}
						</dl>
					</InspectorSection>

					{/* Input Parameters - All users */}
					<InspectorSection title="Input Parameters">
						<PrettyInputDisplay
							inputData={inputData as Record<string, unknown>}
							showToggle={true}
							defaultView="pretty"
						/>
					</InspectorSection>
				</>
			)}

			{/* Execution Context - Platform admins only */}
			{executionContext && (
				<motion.div
					initial={{ opacity: 0, y: 20 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: 0.3, delay: 0.1 }}
				>
					<InspectorSection
						title="Execution Context"
						description="The context object available to this workflow (admin only)"
						action={
							<Popover>
								<PopoverTrigger asChild>
									<Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground">
										<Info className="h-4 w-4" />
									</Button>
								</PopoverTrigger>
								<PopoverContent side="left" align="start" className="w-auto max-w-sm p-0 border-none">
									<SyntaxHighlighter
										language="python"
										style={oneDark}
										customStyle={{ margin: 0, borderRadius: "0.375rem", fontSize: "0.75rem" }}
									>
{`from bifrost import context

context.parameters       # input params + _event
context.parameters["_event"]  # webhook metadata
context.org_id           # organization scope
context.email            # caller email
context.roi.time_saved   # ROI tracking`}
									</SyntaxHighlighter>
								</PopoverContent>
							</Popover>
						}
					>
						<div className="rounded-lg bg-muted/50 ring-1 ring-foreground/5 p-3">
							<VariablesTreeView
								data={executionContext as Record<string, unknown>}
							/>
						</div>
					</InspectorSection>
				</motion.div>
			)}

			{/* Runtime Variables - Platform admins only */}
			{isPlatformAdmin && isComplete && (
				<motion.div
					initial={{ opacity: 0, y: 20 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: 0.3, delay: 0.2 }}
				>
					<InspectorSection
						title="Runtime Variables"
						description="Variables captured from script namespace (admin only)"
					>
						<div className="rounded-lg bg-muted/50 ring-1 ring-foreground/5 p-3">
							<AnimatePresence mode="wait">
								{isLoading ? (
									<motion.div
										key="loading"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="space-y-2"
									>
										<Skeleton className="h-4 w-full" />
										<Skeleton className="h-4 w-4/5" />
										<Skeleton className="h-4 w-3/4" />
									</motion.div>
								) : !variablesData ||
								  Object.keys(variablesData)
										.length === 0 ? (
									<motion.div
										key="empty"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="text-center text-muted-foreground py-8"
									>
										No variables captured
									</motion.div>
								) : (
									<motion.div
										key="content"
										initial={{ opacity: 0 }}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: 0.2,
										}}
										className="overflow-x-auto"
									>
										<VariablesTreeView
											data={
												variablesData as Record<
													string,
													unknown
												>
											}
										/>
									</motion.div>
								)}
							</AnimatePresence>
						</div>
					</InspectorSection>
				</motion.div>
			)}

			{/* Usage Card - Compute resources (admin) + AI usage (all users) */}
			{isComplete &&
				((isPlatformAdmin &&
					(peakMemoryBytes ||
						cpuTotalSeconds)) ||
					(aiUsage &&
						aiUsage.length > 0)) && (
					<motion.div
						initial={{ opacity: 0, y: 20 }}
						animate={{ opacity: 1, y: 0 }}
						transition={{ duration: 0.3, delay: 0.2 }}
					>
						<InspectorSection
							title="Usage"
							description="Execution metrics and costs"
						>
							<div className="rounded-lg bg-muted/50 ring-1 ring-foreground/5 p-3 space-y-3">
								{/* Compute Resources - Platform admins only */}
								{isPlatformAdmin &&
									(peakMemoryBytes ||
										cpuTotalSeconds) && (
										<div className="space-y-3">
											{peakMemoryBytes && (
												<div>
													<p className="text-sm font-medium text-muted-foreground">
														Memory
													</p>
													<p className="text-sm font-mono">
														{formatBytes(
															peakMemoryBytes,
														)}
													</p>
												</div>
											)}
											{cpuTotalSeconds && (
												<div>
													<p className="text-sm font-medium text-muted-foreground">
														CPU Time
													</p>
													<p className="text-sm font-mono">
														{cpuTotalSeconds.toFixed(
															3,
														)}
														s
													</p>
												</div>
											)}
										</div>
									)}

								{/* Divider when both sections are shown */}
								{isPlatformAdmin &&
									(peakMemoryBytes ||
										cpuTotalSeconds) &&
									aiUsage &&
									aiUsage.length >
										0 && (
										<div className="border-t pt-4" />
									)}

								{/* AI Usage - Available to all users */}
								{aiUsage &&
									aiUsage.length >
										0 && (
										<Collapsible
											open={isAiUsageOpen}
											onOpenChange={
												setIsAiUsageOpen
											}
										>
											<div className="flex items-center justify-between">
												<div className="flex items-center gap-2">
													<Sparkles className="h-4 w-4 text-purple-500" />
													<span className="text-sm font-medium">
														AI Usage
													</span>
													<Badge
														variant="secondary"
														className="text-xs"
													>
														{aiTotals
															?.call_count ||
															aiUsage
																.length}{" "}
														{(aiTotals
															?.call_count ||
															aiUsage
																.length) ===
														1
															? "call"
															: "calls"}
													</Badge>
												</div>
												<CollapsibleTrigger
													asChild
												>
													<Button
														variant="ghost"
														size="sm"
													>
														<ChevronDown
															className={`h-4 w-4 transition-transform duration-200 ${
																isAiUsageOpen
																	? "rotate-180"
																	: ""
															}`}
														/>
													</Button>
												</CollapsibleTrigger>
											</div>
											{aiTotals && (
												<p className="mt-1 text-xs text-muted-foreground">
													Total:{" "}
													{formatNumber(
														aiTotals
															.total_input_tokens,
													)}{" "}
													in /{" "}
													{formatNumber(
														aiTotals
															.total_output_tokens,
													)}{" "}
													out tokens
													{aiTotals
														.total_cost &&
														` | ${formatCost(aiTotals.total_cost)}`}
												</p>
											)}
											<CollapsibleContent>
												<div className="mt-3 overflow-x-auto">
													<table className="w-full text-xs">
														<thead>
															<tr className="border-b">
																<th className="text-left py-2 pr-2 font-medium text-muted-foreground">
																	Model
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	Calls
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	In
																</th>
																<th className="text-right py-2 pr-2 font-medium text-muted-foreground">
																	Out
																</th>
																<th className="text-right py-2 font-medium text-muted-foreground">
																	Cost
																</th>
															</tr>
														</thead>
														<tbody>
															{groupedAiUsage.map(
																(
																	group,
																	index,
																) => (
																	<tr
																		key={
																			index
																		}
																		className="border-b last:border-0"
																	>
																		<td className="py-2 pr-2 font-mono text-muted-foreground">
																			{group
																				.model
																				.length >
																			20
																				? `${group.model.substring(0, 18)}...`
																				: group.model}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{group.calls}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{formatNumber(
																				group.input_tokens,
																			)}
																		</td>
																		<td className="py-2 pr-2 text-right font-mono">
																			{formatNumber(
																				group.output_tokens,
																			)}
																		</td>
																		<td className="py-2 text-right font-mono">
																			{formatCost(
																				group.cost,
																			)}
																		</td>
																	</tr>
																),
															)}
														</tbody>
														{aiTotals && (
															<tfoot>
																<tr className="bg-muted/50 font-medium">
																	<td
																		colSpan={
																			2
																		}
																		className="py-2 pr-2"
																	>
																		Total
																	</td>
																	<td className="py-2 pr-2 text-right font-mono">
																		{formatNumber(
																			aiTotals
																				.total_input_tokens,
																		)}
																	</td>
																	<td className="py-2 pr-2 text-right font-mono">
																		{formatNumber(
																			aiTotals
																				.total_output_tokens,
																		)}
																	</td>
																	<td className="py-2 text-right font-mono">
																		{formatCost(
																			aiTotals
																				.total_cost,
																		)}
																	</td>
																</tr>
															</tfoot>
														)}
													</table>
												</div>
											</CollapsibleContent>
										</Collapsible>
									)}
							</div>
						</InspectorSection>
					</motion.div>
				)}
		</div>
	);
}
