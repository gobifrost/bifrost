import { Braces, Gauge, Info, Workflow, type LucideIcon } from "lucide-react";
import { ExecutionSectionHeading } from "./ExecutionSectionHeading";
import { type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { ExecutionAiUsage } from "./ExecutionAiUsage";
import { Skeleton } from "@/components/ui/skeleton";
import { PrettyInputDisplay } from "./PrettyInputDisplay";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { formatBytes } from "@/lib/utils";
import { ExecutionMetadata } from "./ExecutionMetadata";
import { ExecutionContextHelp } from "./ExecutionContextHelp";
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
	/** Whether diagnostic execution data is readable for this run */
	canReadDiagnostics: boolean;
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
 * icon heading (with optional muted description and trailing action),
 * then content that carries a single step-1 surface.
 */
function InspectorSection({
	title,
	icon = Info,
	description,
	action,
	children,
}: {
	title: string;
	icon?: LucideIcon;
	description?: string;
	action?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section>
			<ExecutionSectionHeading
				title={title}
				icon={icon}
				description={description}
				action={action}
			/>
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
	canReadDiagnostics,
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
	const reduceMotion = useReducedMotion();
	const revealMotion = reduceMotion
		? {
				initial: false,
				animate: { opacity: 1, y: 0 },
				transition: { duration: 0 },
			}
		: {
				initial: { opacity: 0, y: 12 },
				animate: { opacity: 1, y: 0 },
				transition: { duration: 0.2 },
			};

	return (
		<div className="min-w-0 space-y-5">
			{!extrasOnly && (
				<>
					{/* Details — dense definition list: one step-1 surface,
					    hairline-separated rows. The workflow name and status
					    live in the page header; repeating them here was pure
					    duplication. */}
					<InspectorSection title="Details">
						<ExecutionMetadata
							executedByName={executedByName}
							orgName={orgName}
							scheduledAt={scheduledAt}
							startedAt={startedAt}
							completedAt={completedAt}
							durationMs={durationMs}
						/>
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

			{/* Execution Context - callers with diagnostic access */}
			{canReadDiagnostics && executionContext && (
				<motion.div {...revealMotion}>
					<InspectorSection
						title="Execution Context"
						icon={Workflow}
						description="The context object available to this workflow"
						action={<ExecutionContextHelp />}
					>
						<div className="min-w-0 rounded-[var(--bf-radius-surface)] border border-border bg-muted/50 p-3">
							<VariablesTreeView
								data={
									executionContext as Record<string, unknown>
								}
							/>
						</div>
					</InspectorSection>
				</motion.div>
			)}

			{/* Runtime Variables - callers with diagnostic access */}
			{canReadDiagnostics && isComplete && (
				<motion.div {...revealMotion}>
					<InspectorSection
						title="Runtime Variables"
						icon={Braces}
						description="Variables captured from the script namespace"
					>
						<div className="min-w-0 rounded-[var(--bf-radius-surface)] border border-border bg-muted/50 p-3">
							<AnimatePresence mode="wait">
								{isLoading ? (
									<motion.div
										key="loading"
										initial={
											reduceMotion
												? false
												: { opacity: 0 }
										}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: reduceMotion ? 0 : 0.22,
										}}
										className="space-y-2"
									>
										<Skeleton className="h-4 w-full" />
										<Skeleton className="h-4 w-4/5" />
										<Skeleton className="h-4 w-3/4" />
									</motion.div>
								) : !variablesData ||
								  Object.keys(variablesData).length === 0 ? (
									<motion.div
										key="empty"
										initial={
											reduceMotion
												? false
												: { opacity: 0 }
										}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: reduceMotion ? 0 : 0.22,
										}}
										className="text-center text-muted-foreground py-8"
									>
										No variables captured
									</motion.div>
								) : (
									<motion.div
										key="content"
										initial={
											reduceMotion
												? false
												: { opacity: 0 }
										}
										animate={{ opacity: 1 }}
										exit={{ opacity: 0 }}
										transition={{
											duration: reduceMotion ? 0 : 0.22,
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
				((canReadDiagnostics &&
					(peakMemoryBytes != null || cpuTotalSeconds != null)) ||
					(aiUsage && aiUsage.length > 0)) && (
					<motion.div {...revealMotion}>
						<InspectorSection
							title="Usage"
							icon={Gauge}
							description="Execution metrics and costs"
						>
							<div className="min-w-0 rounded-[var(--bf-radius-surface)] border border-border bg-muted/50 p-3 space-y-3">
								{/* Compute Resources - Platform admins only */}
								{canReadDiagnostics &&
									(peakMemoryBytes != null ||
										cpuTotalSeconds != null) && (
										<div className="space-y-3">
											{peakMemoryBytes != null && (
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
											{cpuTotalSeconds != null && (
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
								{canReadDiagnostics &&
									(peakMemoryBytes != null ||
										cpuTotalSeconds != null) &&
									aiUsage &&
									aiUsage.length > 0 && (
										<div className="border-t pt-4" />
									)}

								{/* AI Usage - Available to all users */}
								{aiUsage && aiUsage.length > 0 && (
									<ExecutionAiUsage
										usage={aiUsage}
										totals={aiTotals}
									/>
								)}
							</div>
						</InspectorSection>
					</motion.div>
				)}
		</div>
	);
}
