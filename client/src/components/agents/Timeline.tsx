/**
 * Timeline has two deliberately different projections of a run:
 *
 * - Timeline: a user-facing activity story. It groups the executor's
 *   decision/call/result records into one operation and nests child runs.
 * - AdvancedTimeline: the exact step sequence with raw payload disclosure.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
	AlertCircle,
	ArrowUpRight,
	Bot,
	Check,
	ChevronRight,
	CircleDot,
	Code2,
	Cpu,
	GitBranch,
	Loader2,
	MessageSquare,
	MessageSquareText,
	TriangleAlert,
	Wrench,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { formatDuration, formatNumber } from "@/lib/utils";
import type { components } from "@/lib/v1";
import {
	createAgentRunNavigationState,
	type AgentRunNavigationOrigin,
} from "@/lib/agent-run-navigation";
import { useAgentRun } from "@/services/agentRuns";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";

import { DidNarrative } from "./DidNarrative";
import { isEmptyJson } from "./JsonTree";
import {
	activityDomId,
	buildActivityReferenceIndex,
	buildRunActivity,
	type RunActivityItem,
} from "./run-activity";

type AgentRunStepResponse = components["schemas"]["AgentRunStepResponse"];
type AgentRunDetailResponse = components["schemas"]["AgentRunDetailResponse"];
type AgentRunChildResponse = components["schemas"]["AgentRunChildResponse"];

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancelling"]);

export interface TimelineProps {
	steps: AgentRunStepResponse[] | null | undefined;
	childRunIds?: string[] | null;
	childRuns?: AgentRunChildResponse[] | null;
	runStatus?: string | null;
	showTechnicalDetails?: boolean;
	highlightedActivityId?: string | null;
	expandedDelegationIds?: ReadonlySet<string>;
	onDelegationExpandedChange?: (
		activityId: string,
		expanded: boolean,
	) => void;
	restoreActivityId?: string | null;
	onOpenChildRun?: (activityId: string) => void;
	childRunOrigin?: AgentRunNavigationOrigin;
	/** Used only to keep pathological/cyclic history from nesting forever. */
	depth?: number;
}

export function Timeline({
	steps,
	childRunIds = [],
	childRuns = [],
	runStatus,
	showTechnicalDetails = false,
	highlightedActivityId = null,
	expandedDelegationIds,
	onDelegationExpandedChange,
	restoreActivityId = null,
	onOpenChildRun,
	childRunOrigin,
	depth = 0,
}: TimelineProps) {
	const activity = buildRunActivity(steps, childRunIds, childRuns);
	if (!activity.length) {
		return (
			<div className="rounded-lg border border-dashed px-4 py-5 text-center">
				<p className="text-sm font-medium">No activity to summarize</p>
				<p className="mt-1 text-xs text-muted-foreground">
					Any recorded executor steps are still available in Advanced.
				</p>
			</div>
		);
	}

	return (
		<ol
			className="relative grid gap-3 before:absolute before:bottom-5 before:left-[15px] before:top-5 before:w-px before:bg-border"
			aria-label="Run activity"
		>
			{activity.map((item) => (
				<ActivityRow
					key={item.id}
					item={item}
					depth={depth}
					runStatus={runStatus}
					showTechnicalDetails={showTechnicalDetails}
					highlighted={item.id === highlightedActivityId}
					expandedDelegationIds={expandedDelegationIds}
					onDelegationExpandedChange={onDelegationExpandedChange}
					restoreActivityId={restoreActivityId}
					onOpenChildRun={onOpenChildRun}
					childRunOrigin={childRunOrigin}
				/>
			))}
		</ol>
	);
}

function ActivityRow({
	item,
	depth,
	runStatus,
	showTechnicalDetails,
	highlighted,
	expandedDelegationIds,
	onDelegationExpandedChange,
	restoreActivityId,
	onOpenChildRun,
	childRunOrigin,
}: {
	item: RunActivityItem;
	depth: number;
	runStatus?: string | null;
	showTechnicalDetails: boolean;
	highlighted: boolean;
	expandedDelegationIds?: ReadonlySet<string>;
	onDelegationExpandedChange?: (
		activityId: string,
		expanded: boolean,
	) => void;
	restoreActivityId?: string | null;
	onOpenChildRun?: (activityId: string) => void;
	childRunOrigin?: AgentRunNavigationOrigin;
}) {
	if (item.kind === "delegation") {
		return (
			<DelegationRow
				item={item}
				depth={depth}
				showTechnicalDetails={showTechnicalDetails}
				highlighted={highlighted}
				expandedDelegationIds={expandedDelegationIds}
				onDelegationExpandedChange={onDelegationExpandedChange}
				restoreActivityId={restoreActivityId}
				onOpenChildRun={onOpenChildRun}
				childRunOrigin={childRunOrigin}
			/>
		);
	}

	const isError = item.isError || item.kind === "error";
	const isWarning = item.kind === "warning";
	const isCancelled = item.kind === "cancelled";
	const isResponse = item.kind === "response";
	const pending = item.kind === "action" && !item.resultStep;
	const runInProgress = ["queued", "running", "cancelling"].includes(
		runStatus ?? "",
	);
	const Icon = isError
		? AlertCircle
		: isWarning || isCancelled
			? TriangleAlert
			: isResponse
				? MessageSquareText
				: pending
					? CircleDot
					: Check;
	const tone = isError
		? "border-rose-500/20 bg-rose-500/[0.06]"
		: isWarning || isCancelled
			? "border-amber-500/20 bg-amber-500/[0.06]"
			: "border-border/70 bg-card";
	const iconTone = isError
		? "border-rose-500/25 bg-rose-500/15 text-rose-600 dark:text-rose-300"
		: isWarning || isCancelled
			? "border-amber-500/25 bg-amber-500/15 text-amber-600 dark:text-amber-300"
			: isResponse
				? "border-violet-500/25 bg-violet-500/15 text-violet-600 dark:text-violet-300"
				: pending
					? runInProgress
						? "border-blue-500/25 bg-blue-500/15 text-blue-600 dark:text-blue-300"
						: "border-border bg-muted text-muted-foreground"
					: "border-emerald-500/25 bg-emerald-500/15 text-emerald-600 dark:text-emerald-300";

	return (
		<li
			id={activityDomId(item.id)}
			tabIndex={-1}
			className="relative scroll-mt-24 rounded-xl pl-9 outline-none"
			data-activity-id={item.id}
			data-activity-kind={item.kind}
			data-highlighted={highlighted ? "true" : "false"}
		>
			<div
				className={cn(
					"absolute left-0 top-4 z-10 grid h-[31px] w-[31px] place-items-center rounded-full border shadow-sm",
					iconTone,
				)}
			>
				<Icon className="h-3.5 w-3.5" />
			</div>
			<div
				className={cn(
					"rounded-xl border px-4 py-3 shadow-sm transition-[border-color,background-color,box-shadow] duration-150 motion-reduce:transition-none",
					tone,
					highlighted &&
						"border-blue-500/50 ring-2 ring-blue-500/45 ring-offset-2 ring-offset-background shadow-[0_0_24px_-4px] shadow-blue-500/40",
				)}
			>
				<div className="flex items-start gap-3">
					<div className="min-w-0 flex-1">
						<div className="text-sm font-medium leading-5">
							{item.title}
						</div>
						{item.description ? (
							<p className="mt-1 text-[13px] leading-5 text-muted-foreground">
								{item.description}
							</p>
						) : pending ? (
							<p className="mt-1 text-xs text-muted-foreground">
								{runInProgress
									? "In progress"
									: "No outcome recorded"}
							</p>
						) : null}
					</div>
					<div className="flex shrink-0 items-center gap-2 pt-0.5">
						{item.durationMs != null ? (
							<span className="text-[11px] tabular-nums text-muted-foreground">
								{formatDuration(item.durationMs)}
							</span>
						) : null}
						{item.executionId ? (
							<Link
								to={`/history/${item.executionId}`}
								className="inline-flex min-h-11 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-8"
							>
								Execution
								<ArrowUpRight className="h-3 w-3" />
							</Link>
						) : null}
					</div>
				</div>
				{showTechnicalDetails ? (
					<ActivityTechnicalDetails item={item} />
				) : null}
			</div>
		</li>
	);
}

function ActivityTechnicalDetails({ item }: { item: RunActivityItem }) {
	const callContent = (item.callStep?.content ?? {}) as Record<
		string,
		unknown
	>;
	const resultContent = (item.resultStep?.content ?? {}) as Record<
		string,
		unknown
	>;
	const inputDetail = renderDetail(callContent.arguments);
	const resultValue =
		item.resultStep?.type === "llm_response"
			? resultContent.content
			: item.isError
				? (resultContent.error ?? resultContent.result ?? resultContent)
				: (resultContent.result ??
					(item.resultStep && !item.toolName ? resultContent : null));
	const resultDetail = renderDetail(resultValue);
	const stepNumbers = [
		item.callStep?.step_number,
		item.resultStep?.step_number,
	].filter((value): value is number => value != null);
	const tokens = [item.callStep, item.resultStep].reduce(
		(total, step) => total + (step?.tokens_used ?? 0),
		0,
	);
	const hasMetadata = !!item.toolName || stepNumbers.length > 0 || tokens > 0;

	if (!hasMetadata && !inputDetail && !resultDetail) return null;

	return (
		<details className="group mt-3 border-t border-border/70 pt-2.5">
			<summary className="flex min-h-6 cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
				<ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
				<Code2 className="h-3 w-3" />
				Details
			</summary>
			<div className="mt-2.5 grid gap-3 rounded-lg bg-background/65 p-3 ring-1 ring-foreground/5">
				{hasMetadata ? (
					<dl className="grid gap-x-4 gap-y-1.5 text-[11px] sm:grid-cols-[auto_1fr]">
						{item.toolName ? (
							<>
								<dt className="text-muted-foreground">
									Internal action
								</dt>
								<dd className="min-w-0 break-all font-mono">
									{item.toolName}
								</dd>
							</>
						) : null}
						{stepNumbers.length ? (
							<>
								<dt className="text-muted-foreground">Trace</dt>
								<dd>
									{stepNumbers.length === 1 ||
									stepNumbers[0] === stepNumbers.at(-1)
										? `Step ${stepNumbers[0]}`
										: `Steps ${stepNumbers[0]}–${stepNumbers.at(-1)}`}
								</dd>
							</>
						) : null}
						{tokens > 0 ? (
							<>
								<dt className="text-muted-foreground">
									Tokens
								</dt>
								<dd>{formatNumber(tokens)}</dd>
							</>
						) : null}
					</dl>
				) : null}
				{inputDetail ? (
					<TechnicalDetailSection
						label="Input"
						detail={inputDetail}
					/>
				) : null}
				{resultDetail ? (
					<TechnicalDetailSection
						label={item.isError ? "Error" : "Result"}
						detail={resultDetail}
					/>
				) : null}
			</div>
		</details>
	);
}

function TechnicalDetailSection({
	label,
	detail,
}: {
	label: string;
	detail: DetailRender;
}) {
	return (
		<section>
			<div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
				{label}
			</div>
			<DetailBlock detail={detail} />
		</section>
	);
}

function DelegationRow({
	item,
	depth,
	showTechnicalDetails,
	highlighted,
	expandedDelegationIds,
	onDelegationExpandedChange,
	restoreActivityId,
	onOpenChildRun,
	childRunOrigin,
}: {
	item: RunActivityItem;
	depth: number;
	showTechnicalDetails: boolean;
	highlighted: boolean;
	expandedDelegationIds?: ReadonlySet<string>;
	onDelegationExpandedChange?: (
		activityId: string,
		expanded: boolean,
	) => void;
	restoreActivityId?: string | null;
	onOpenChildRun?: (activityId: string) => void;
	childRunOrigin?: AgentRunNavigationOrigin;
}) {
	const [localOpen, setLocalOpen] = useState(false);
	const rowRef = useRef<HTMLLIElement>(null);
	const open =
		expandedDelegationIds !== undefined
			? expandedDelegationIds.has(item.id)
			: localOpen;

	useEffect(() => {
		if (restoreActivityId !== item.id) return;
		rowRef.current?.scrollIntoView({
			behavior: "auto",
			block: "center",
		});
	}, [item.id, restoreActivityId]);

	function toggleOpen() {
		const nextOpen = !open;
		if (onDelegationExpandedChange) {
			onDelegationExpandedChange(item.id, nextOpen);
			return;
		}
		setLocalOpen(nextOpen);
	}

	const {
		data: rawChild,
		isLoading,
		isError,
	} = useAgentRun(open ? (item.childRunId ?? undefined) : undefined, {
		refetchInterval: (query) =>
			ACTIVE_RUN_STATUSES.has(query.state.data?.status ?? "")
				? 2_000
				: false,
	});
	const child = rawChild as unknown as AgentRunDetailResponse | undefined;
	const agentName = child?.agent_name ?? item.agentName;
	const childAgentId = child?.agent_id ?? item.childAgentId;
	const title = agentName ?? "Delegated agent";
	const expandable = !!item.childRunId;
	const childStatus = child?.status ?? item.childStatus;
	const childFailed = childStatus ? isFailedRunStatus(childStatus) : false;
	const delegationFailed = item.isError || childFailed;
	const delegationStatus = childStatus ?? (item.isError ? "failed" : null);
	const delegationStatusId = delegationStatus
		? `${activityDomId(item.id)}-status`
		: undefined;
	const detailsId = `${activityDomId(item.id)}-details`;
	const childActivity = child
		? buildRunActivity(child.steps, child.child_run_ids, child.child_runs)
		: [];
	const childActivityReferences = buildActivityReferenceIndex(childActivity);
	const rowContent = (
		<>
			<div className="min-w-0 flex-1">
				<div className="flex flex-wrap items-center gap-x-2 gap-y-1">
					<span className="min-w-0 break-words text-sm font-medium leading-5">
						{title}
					</span>
					<span
						className={cn(
							"shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
							delegationFailed
								? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
								: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
						)}
					>
						Agent
					</span>
					{delegationStatus ? (
						<DelegationStatusBadge
							id={delegationStatusId}
							status={delegationStatus}
						/>
					) : null}
				</div>
				{item.task ? (
					<p className="mt-1 text-[13px] leading-5 text-muted-foreground">
						{item.task}
					</p>
				) : item.description ? (
					<p className="mt-1 text-[13px] leading-5 text-muted-foreground">
						{item.description}
					</p>
				) : null}
			</div>
			{!delegationStatus && item.durationMs != null ? (
				<div className="shrink-0 pt-0.5 text-[11px] text-muted-foreground">
					<span>{formatDuration(item.durationMs)}</span>
				</div>
			) : null}
		</>
	);

	return (
		<li
			ref={rowRef}
			id={activityDomId(item.id)}
			tabIndex={-1}
			className="relative scroll-mt-24 rounded-xl pl-9 outline-none"
			data-activity-id={item.id}
			data-activity-kind="delegation"
			data-highlighted={highlighted ? "true" : "false"}
		>
			<div
				className={cn(
					"absolute left-0 top-4 z-10 grid h-[31px] w-[31px] place-items-center rounded-full border shadow-sm",
					delegationFailed
						? "border-rose-500/25 bg-rose-500/15 text-rose-600 dark:text-rose-300"
						: "border-violet-500/25 bg-violet-500/15 text-violet-600 dark:text-violet-300",
				)}
			>
				<GitBranch className="h-3.5 w-3.5" />
			</div>
			<div
				className={cn(
					"overflow-hidden rounded-xl border shadow-sm transition-[border-color,background-color,box-shadow] duration-150 motion-reduce:transition-none",
					delegationFailed
						? "border-rose-500/20 bg-rose-500/[0.055]"
						: "border-violet-500/20 bg-violet-500/[0.055]",
					highlighted &&
						"border-violet-500/55 ring-2 ring-violet-500/45 ring-offset-2 ring-offset-background shadow-[0_0_24px_-4px] shadow-violet-500/40",
				)}
			>
				<div className="flex flex-col md:flex-row md:items-stretch">
					{expandable ? (
						<button
							type="button"
							onClick={toggleOpen}
							aria-expanded={open}
							aria-controls={detailsId}
							aria-label={`${open ? "Hide" : "Show"} details for ${title}`}
							aria-describedby={delegationStatusId}
							className={cn(
								"group flex min-h-11 min-w-0 flex-1 cursor-pointer items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-violet-500/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
								open && "bg-violet-500/[0.045]",
							)}
						>
							{rowContent}
							{isLoading && open ? (
								<Loader2 className="h-4 w-4 shrink-0 self-center animate-spin text-violet-600 motion-reduce:animate-none dark:text-violet-300" />
							) : (
								<ChevronRight
									className={cn(
										"h-4 w-4 shrink-0 self-center text-muted-foreground transition-transform group-hover:text-foreground motion-reduce:transition-none",
										open && "rotate-90",
									)}
								/>
							)}
						</button>
					) : (
						<div className="flex min-w-0 flex-1 items-start gap-3 px-4 py-3 text-left">
							{rowContent}
						</div>
					)}
					{childAgentId && item.childRunId ? (
						<div className="flex shrink-0 items-center justify-end border-t border-violet-500/15 p-2 md:border-l md:border-t-0">
							<Link
								to={`/agents/${childAgentId}/runs/${item.childRunId}`}
								state={
									childRunOrigin
										? createAgentRunNavigationState(
												childRunOrigin,
											)
										: undefined
								}
								onClick={() => onOpenChildRun?.(item.id)}
								aria-label={`Open ${title} run`}
								className="inline-flex min-h-11 items-center gap-1 rounded-md px-3 text-xs font-medium text-primary transition-colors hover:bg-violet-500/10 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:min-h-8"
							>
								<span>Open run</span>
								<ArrowUpRight className="h-3.5 w-3.5" />
							</Link>
						</div>
					) : null}
				</div>

				{open ? (
					<div
						id={detailsId}
						className="border-t border-violet-500/15 bg-background/45 px-4 py-4"
					>
						{isLoading ? (
							<div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
								<Loader2 className="h-3.5 w-3.5 animate-spin" />
								Loading delegated work…
							</div>
						) : isError || !child ? (
							<p className="py-2 text-xs text-rose-600 dark:text-rose-300">
								Delegated run details are not available.
							</p>
						) : (
							<div className="grid gap-4">
								<div className="grid gap-2 sm:grid-cols-2">
									<DelegationSummary label="Task">
										{child.asked ??
											item.task ??
											"No task summary recorded."}
									</DelegationSummary>
									<DelegationSummary label="Outcome">
										<DidNarrative
											text={child.did ?? child.answered}
											activityReferences={
												childActivityReferences
											}
											compact
											fallback={
												<>
													No outcome summary recorded.
												</>
											}
										/>
									</DelegationSummary>
								</div>

								{depth < 3 &&
								((child.steps?.length ?? 0) > 0 ||
									(child.child_run_ids?.length ?? 0) > 0) ? (
									<div className="rounded-lg border bg-background/65 p-3">
										<div className="mb-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
											Activity
										</div>
										<Timeline
											steps={child.steps}
											childRunIds={child.child_run_ids}
											childRuns={child.child_runs}
											runStatus={child.status}
											showTechnicalDetails={
												showTechnicalDetails
											}
											expandedDelegationIds={
												expandedDelegationIds
											}
											onDelegationExpandedChange={
												onDelegationExpandedChange
											}
											restoreActivityId={
												restoreActivityId
											}
											onOpenChildRun={onOpenChildRun}
											childRunOrigin={childRunOrigin}
											depth={depth + 1}
										/>
									</div>
								) : null}
							</div>
						)}
					</div>
				) : null}
			</div>
		</li>
	);
}

function DelegationStatusBadge({
	id,
	status,
}: {
	id: string | undefined;
	status: string;
}) {
	const label = runStatusLabel(status);
	const failed = isFailedRunStatus(status);
	const active = ACTIVE_RUN_STATUSES.has(status);
	const completed = status === "completed";
	const StatusIcon = failed
		? AlertCircle
		: status === "running" || status === "cancelling"
			? Loader2
			: completed
				? Check
				: CircleDot;

	return (
		<span
			id={id}
			aria-label={`Delegated run status: ${label}`}
			className={cn(
				"inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium",
				failed
					? "bg-rose-500/10 text-rose-700 dark:text-rose-300"
					: active
						? "bg-blue-500/10 text-blue-700 dark:text-blue-300"
						: completed
							? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
							: "bg-muted text-muted-foreground",
			)}
		>
			<StatusIcon
				aria-hidden="true"
				className={cn(
					"h-2.5 w-2.5",
					(status === "running" || status === "cancelling") &&
						"animate-spin motion-reduce:animate-none",
				)}
			/>
			{label}
		</span>
	);
}

function isFailedRunStatus(status: string): boolean {
	return [
		"failed",
		"budget_exceeded",
		"cancelled",
		"timeout",
		"timed_out",
	].includes(status);
}

function runStatusLabel(status: string): string {
	switch (status) {
		case "queued":
			return "Queued";
		case "running":
			return "Running";
		case "cancelling":
			return "Cancelling";
		case "cancelled":
			return "Cancelled";
		case "failed":
			return "Failed";
		case "budget_exceeded":
			return "Budget exceeded";
		case "timeout":
		case "timed_out":
			return "Timed out";
		case "completed":
			return "Completed";
		default:
			return status.replace(/_/g, " ");
	}
}

function DelegationSummary({
	label,
	children,
}: {
	label: string;
	children: ReactNode;
}) {
	return (
		<div className="rounded-lg bg-background/70 px-3 py-2.5 ring-1 ring-foreground/5">
			<div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
				{label}
			</div>
			<div className="text-xs leading-5">{children}</div>
		</div>
	);
}

type DetailRender =
	{ kind: "json"; value: unknown } | { kind: "text"; value: string };

interface StepViewModel {
	icon: typeof Wrench;
	iconClass: string;
	label: string;
	summary: string | null;
	primaryDetail: DetailRender | null;
	secondaryDetail: (DetailRender & { label: string }) | null;
}

function buildViewModel(step: AgentRunStepResponse): StepViewModel {
	const c = (step.content ?? {}) as Record<string, unknown>;
	const type = step.type ?? "step";

	switch (type) {
		case "tool_call": {
			const name = (c.tool_name as string) || "tool";
			const args = c.arguments;
			return {
				icon: Wrench,
				iconClass: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
				label: `Called ${name}`,
				summary: null,
				primaryDetail: isEmptyJson(args)
					? null
					: { kind: "json", value: args },
				secondaryDetail: null,
			};
		}
		case "tool_result": {
			const name = (c.tool_name as string) || "tool";
			const result = c.result;
			return {
				icon: CircleDot,
				iconClass:
					"bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
				label: `Result from ${name}`,
				summary: inlineTextPreview(result, 100),
				primaryDetail: renderDetail(result),
				secondaryDetail: null,
			};
		}
		case "tool_error": {
			const name = (c.tool_name as string) || "tool";
			const error = c.error ?? c.result;
			return {
				icon: AlertCircle,
				iconClass: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
				label: `Error from ${name}`,
				summary: inlineTextPreview(error, 100),
				primaryDetail: renderDetail(error),
				secondaryDetail: null,
			};
		}
		case "llm_request": {
			const model = (c.model as string | null) ?? null;
			const tools = (c.tools_count as number | undefined) ?? null;
			const messages = (c.messages_count as number | undefined) ?? null;
			const bits: string[] = [];
			if (model) bits.push(model);
			if (messages != null) bits.push(`${messages} msgs`);
			if (tools != null) bits.push(`${tools} tools`);
			return {
				icon: Cpu,
				iconClass: "bg-muted text-muted-foreground",
				label: "LLM request",
				summary: bits.length ? bits.join(" · ") : null,
				primaryDetail: { kind: "json", value: c },
				secondaryDetail: null,
			};
		}
		case "llm_response": {
			const text = (c.content as string | undefined) ?? "";
			const toolCalls =
				(c.tool_calls as
					| Array<{
							name?: string;
					  }>
					| undefined) ?? [];
			const callNames = toolCalls
				.map((tc) => tc.name)
				.filter(Boolean) as string[];
			// Label: when the LLM picked tools, name them directly.
			// "Decided to call get_ticket, send_email" beats the abstract
			// "LLM decided to call tools" by one click of comprehension.
			const label =
				callNames.length > 0
					? `Decided to call ${callNames.join(", ")}`
					: text
						? "Reasoned"
						: "LLM response";
			return {
				icon: Bot,
				iconClass:
					"bg-violet-500/15 text-violet-600 dark:text-violet-400",
				label,
				// If we put the names in the label, no summary needed; show the
				// reasoning text as summary when it's the standalone case.
				summary:
					callNames.length > 0 ? null : inlineTextPreview(text, 120),
				primaryDetail: text ? { kind: "text", value: text } : null,
				secondaryDetail:
					callNames.length > 0
						? {
								kind: "json",
								label: "Tool calls",
								value: toolCalls,
							}
						: null,
			};
		}
		case "error":
		case "budget_warning":
		case "cancelled": {
			return {
				icon: AlertCircle,
				iconClass:
					type === "error"
						? "bg-rose-500/15 text-rose-600 dark:text-rose-400"
						: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
				label:
					type === "cancelled"
						? "Cancelled"
						: type === "budget_warning"
							? "Budget warning"
							: "Error",
				summary: errorSummary(c),
				primaryDetail: { kind: "json", value: c },
				secondaryDetail: null,
			};
		}
		default: {
			return {
				icon: MessageSquare,
				iconClass: "bg-muted text-muted-foreground",
				label: type,
				summary: null,
				primaryDetail: { kind: "json", value: c },
				secondaryDetail: null,
			};
		}
	}
}

function renderDetail(value: unknown): DetailRender | null {
	if (value === null || value === undefined || value === "") return null;
	return typeof value === "string"
		? { kind: "text", value }
		: { kind: "json", value };
}

function inlineTextPreview(value: unknown, maxLength: number): string | null {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (!trimmed || looksStructured(trimmed)) return null;
	return truncate(trimmed, maxLength);
}

function errorSummary(content: Record<string, unknown>): string | null {
	for (const key of ["message", "error", "reason"]) {
		const preview = inlineTextPreview(content[key], 120);
		if (preview) return preview;
	}
	return null;
}

function truncate(s: string, n: number): string {
	if (s.length <= n) return s;
	return s.slice(0, n - 1) + "…";
}

export interface AdvancedTimelineProps {
	steps: AgentRunStepResponse[] | null | undefined;
}

export function AdvancedTimeline({ steps }: AdvancedTimelineProps) {
	if (!steps || !steps.length) {
		return (
			<p className="text-xs text-muted-foreground">No steps recorded.</p>
		);
	}
	return (
		<ol className="flex min-w-0 flex-col gap-1.5">
			{steps.map((step, i) => (
				<TimelineRow key={step.id ?? i} step={step} index={i + 1} />
			))}
		</ol>
	);
}

function TimelineRow({
	step,
	index,
}: {
	step: AgentRunStepResponse;
	index: number;
}) {
	const [open, setOpen] = useState(false);
	const vm = buildViewModel(step);
	const hasDetail = !!vm.primaryDetail || !!vm.secondaryDetail;
	const Icon = vm.icon;
	return (
		<li className="min-w-0 overflow-hidden rounded-md bg-muted/50 ring-1 ring-foreground/5">
			<button
				type="button"
				onClick={() => hasDetail && setOpen((v) => !v)}
				disabled={!hasDetail}
				aria-expanded={open}
				aria-label={
					hasDetail ? `Toggle details for step ${index}` : undefined
				}
				className={cn(
					"flex min-w-0 w-full items-start gap-2 px-3 py-2 text-left text-xs",
					hasDetail && "hover:bg-accent/40",
				)}
			>
				<div
					className={cn(
						"mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full",
						vm.iconClass,
					)}
				>
					<Icon className="h-3 w-3" />
				</div>
				<div className="min-w-0 flex-1">
					<div className="flex min-w-0 items-baseline gap-2">
						<span
							className="min-w-0 truncate font-medium"
							title={vm.label}
						>
							{vm.label}
						</span>
						{vm.summary ? (
							<span className="truncate text-muted-foreground">
								{vm.summary}
							</span>
						) : null}
					</div>
				</div>
				<span className="ml-auto flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
					{step.tokens_used ? (
						<span title="Tokens used">
							{formatNumber(step.tokens_used)} tok
						</span>
					) : null}
					{step.duration_ms != null ? (
						<span>{formatDuration(step.duration_ms)}</span>
					) : null}
					<span className="font-mono">#{index}</span>
					{hasDetail ? (
						<ChevronRight
							className={cn(
								"h-3 w-3 transition-transform",
								open && "rotate-90",
							)}
						/>
					) : null}
				</span>
			</button>
			{open && hasDetail ? (
				<div className="border-t px-3 py-2">
					{vm.primaryDetail ? (
						<DetailBlock detail={vm.primaryDetail} />
					) : null}
					{vm.secondaryDetail ? (
						<div className="mt-2">
							<div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
								{vm.secondaryDetail.label}
							</div>
							<DetailBlock detail={vm.secondaryDetail} />
						</div>
					) : null}
				</div>
			) : null}
		</li>
	);
}

function DetailBlock({ detail }: { detail: DetailRender }) {
	if (detail.kind === "json") {
		return (
			<div className="max-h-[280px] overflow-y-auto rounded-md bg-muted/60 ring-1 ring-foreground/5 p-2.5">
				<VariablesTreeView data={asVariableRecord(detail.value)} />
			</div>
		);
	}
	const parsed = tryParseJson(detail.value);
	if (parsed !== UNPARSEABLE) {
		return (
			<div className="max-h-[280px] overflow-y-auto rounded-md bg-muted/60 ring-1 ring-foreground/5 p-2.5">
				<VariablesTreeView data={asVariableRecord(parsed)} />
			</div>
		);
	}
	return (
		<div className="max-h-[280px] overflow-y-auto rounded-md bg-muted/60 ring-1 ring-foreground/5 px-3 py-2 text-xs leading-5 whitespace-pre-wrap break-words">
			{detail.value}
		</div>
	);
}

function asVariableRecord(value: unknown): Record<string, unknown> {
	if (value !== null && typeof value === "object" && !Array.isArray(value)) {
		return value as Record<string, unknown>;
	}
	return { value };
}

function looksStructured(value: string): boolean {
	return (
		(value.startsWith("{") && value.endsWith("}")) ||
		(value.startsWith("[") && value.endsWith("]"))
	);
}

const UNPARSEABLE = Symbol("unparseable");
function tryParseJson(value: string): unknown {
	const trimmed = value.trim();
	if (!looksStructured(trimmed)) {
		return UNPARSEABLE;
	}
	try {
		return JSON.parse(trimmed);
	} catch {
		return UNPARSEABLE;
	}
}
