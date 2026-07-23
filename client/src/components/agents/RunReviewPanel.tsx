/**
 * Shared run review panel.
 *
 * Used in three places:
 *   - variant="page": full agent run detail page (main column)
 *   - variant="flipbook": Review flipbook card (full-width inside card)
 *   - variant="drawer": runs sheet (compact side panel)
 *
 * Consistent UX across all three: same summary structure (asked / did / output),
 * same human-readable action fallback, same verdict capture bar.
 */

import { Link } from "react-router-dom";
import {
	User,
	Bot,
	Check,
	CircleDot,
	GitBranch,
	Wrench,
	ThumbsUp,
	ThumbsDown,
	AlertCircle,
	ChevronRight,
	Loader2,
	RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { VariablesTreeView } from "@/components/ui/variables-tree-view";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/utils";
import {
	createAgentRunNavigationState,
	type AgentRunNavigationOrigin,
} from "@/lib/agent-run-navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useRegenerateSummary } from "@/services/agentRuns";
import type { components } from "@/lib/v1";

import { DidNarrative } from "./DidNarrative";
import {
	buildActivityReferenceIndex,
	buildRunActivity,
	type RunActivityItem,
} from "./run-activity";
import { SummaryPlaceholder } from "./SummaryPlaceholder";

export type Verdict = "up" | "down" | null;

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];

export type RunReviewVariant = "page" | "flipbook" | "drawer";

export interface RunReviewPanelProps {
	run: AgentRunDetail;
	verdict: Verdict;
	note: string;
	onVerdict: (v: Verdict) => void;
	onNote: (n: string) => void;
	variant?: RunReviewVariant;
	hideVerdictBar?: boolean;
	runNavigationOrigin?: AgentRunNavigationOrigin;
	onActivityReferencePreview?: (activityId: string | null) => void;
	onActivityReferenceActivate?: (activityId: string) => void;
}

/** Human fallback for runs that predate prose summaries. */
function ActivityFallbackRow({ item }: { item: RunActivityItem }) {
	const delegated = item.kind === "delegation";
	const failed = item.isError || item.kind === "error";
	const completed = !!item.resultStep && !failed;
	const Icon = failed
		? AlertCircle
		: delegated
			? GitBranch
			: completed
				? Check
				: CircleDot;
	const iconTone = failed
		? "bg-rose-500/15 text-rose-600 dark:text-rose-300"
		: delegated
			? "bg-violet-500/15 text-violet-600 dark:text-violet-300"
			: completed
				? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300"
				: "bg-muted text-muted-foreground";
	return (
		<div
			className="flex items-center gap-2 rounded-md bg-muted/50 px-2.5 py-2 text-xs ring-1 ring-foreground/5"
			data-activity-kind={failed ? "error" : item.kind}
		>
			<div
				className={cn(
					"grid h-5 w-5 shrink-0 place-items-center rounded-full",
					iconTone,
				)}
			>
				<Icon className="h-3 w-3" />
			</div>
			<span className="min-w-0 flex-1 font-medium">{item.title}</span>
			{item.durationMs != null ? (
				<span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
					{formatDuration(item.durationMs)}
				</span>
			) : null}
		</div>
	);
}

function payloadText(value: unknown): string {
	if (typeof value === "string") return value;
	try {
		return JSON.stringify(value, null, 2);
	} catch {
		return String(value);
	}
}

export function RunPayloads({
	input,
	output,
	compact = false,
}: {
	input: unknown;
	output: unknown;
	compact?: boolean;
}) {
	const hasInput = input !== null && input !== undefined && input !== "";
	const hasOutput = output !== null && output !== undefined && output !== "";
	if (!hasInput && !hasOutput) return null;
	return (
		<div className="grid gap-2" data-slot="run-payloads">
			{hasInput ? (
				<RawDisclosure
					label="Raw input"
					value={input}
					compact={compact}
				/>
			) : null}
			{hasOutput ? (
				<RawDisclosure
					label="Raw output"
					value={output}
					compact={compact}
				/>
			) : null}
		</div>
	);
}

export function RunReviewPanel({
	run,
	verdict,
	note,
	onVerdict,
	onNote,
	variant = "page",
	hideVerdictBar = false,
	runNavigationOrigin,
	onActivityReferencePreview,
	onActivityReferenceActivate,
}: RunReviewPanelProps) {
	const activity = buildRunActivity(
		run.steps,
		run.child_run_ids,
		run.child_runs,
	);
	const fallbackActions = activity.filter((item) => item.kind !== "response");
	const activityReferences = buildActivityReferenceIndex(activity);
	// Prefer summary prose, safely humanizing any old [tool_name] markers.
	// Runs without prose get a short action list derived from recorded calls;
	// exact identifiers and payloads live only in the detail page's Advanced view.
	const hasDidProse = !!run.did && run.did.trim().length > 0;
	const canVerdict = run.status === "completed" && !hideVerdictBar;
	const compact = variant === "drawer";
	const maxActions = compact ? 3 : 4;
	const visibleActions = fallbackActions.slice(0, maxActions);
	const overflow = fallbackActions.length - visibleActions.length;
	const { isPlatformAdmin } = useAuth();
	const queryClient = useQueryClient();
	const regenSummary = useRegenerateSummary();
	const summaryStatus = run.summary_status;
	const needsRegen = summaryStatus && summaryStatus !== "completed";

	function handleRegenerate() {
		regenSummary.mutate(
			{ params: { path: { run_id: run.id } } },
			{
				onSuccess: () => {
					toast.success("Summary regeneration queued");
					queryClient.invalidateQueries({
						queryKey: ["get", "/api/agent-runs/{run_id}"],
					});
					queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
				},
				onError: () => {
					toast.error("Failed to regenerate summary");
				},
			},
		);
	}

	return (
		<div data-slot="run-review-panel" className="min-w-0">
			<div
				className={cn(
					"grid min-w-0",
					compact ? "gap-3.5 px-4 py-3.5" : "gap-4 px-5 py-4",
				)}
			>
				{needsRegen ? (
					<div
						className={cn(
							// fade-in so the banner doesn't pop when the
							// websocket flips summary_status to generating.
							"flex items-center justify-between gap-3 rounded-md ring-1 px-3 py-2 animate-in fade-in duration-200",
							summaryStatus === "failed"
								? "ring-rose-500/30 bg-rose-500/10"
								: "ring-foreground/5 bg-muted/50",
							compact ? "text-xs" : "text-[13px]",
						)}
					>
						<div className="flex items-center gap-2">
							{summaryStatus === "generating" ||
							regenSummary.isPending ? (
								<Loader2 className="h-3.5 w-3.5 animate-spin" />
							) : (
								<RefreshCw className="h-3.5 w-3.5 text-muted-foreground" />
							)}
							<span>
								{summaryStatus === "failed"
									? "Summary failed"
									: summaryStatus === "generating"
										? "Summary in progress…"
										: "Summary pending"}
							</span>
						</div>
						{/* Hide the Regenerate button while generation is
						    in flight — it would just no-op (idempotent
						    short-circuit on the backend) and looks like the
						    user is being asked to act. */}
						{summaryStatus !== "generating" ? (
							<button
								type="button"
								disabled={
									!isPlatformAdmin || regenSummary.isPending
								}
								title={
									isPlatformAdmin
										? "Re-run summarization"
										: "Only platform admins can regenerate summaries"
								}
								onClick={handleRegenerate}
								className={cn(
									"inline-flex items-center gap-1.5 rounded-2xl border bg-background px-2.5 py-1 text-xs font-medium transition-colors",
									isPlatformAdmin && !regenSummary.isPending
										? "hover:bg-accent"
										: "cursor-not-allowed opacity-60",
								)}
								data-testid="regen-summary-panel-button"
							>
								Regenerate
							</button>
						) : null}
					</div>
				) : null}
				<Section
					icon={<User size={13} />}
					iconClassName="bg-muted text-muted-foreground"
					label="What was asked"
					compact={compact}
				>
					<div
						className={cn(
							"rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-2 whitespace-pre-wrap break-words",
							compact ? "text-xs" : "text-sm",
						)}
					>
						{run.asked || (
							<SummaryPlaceholder
								status={run.summary_status}
								runStatus={run.status}
							/>
						)}
					</div>
				</Section>

				{hasDidProse ? (
					<Section
						icon={<Wrench size={13} />}
						iconClassName="bg-blue-500/15 text-blue-600 dark:text-blue-400"
						label="What the agent did"
						compact={compact}
					>
						<div
							className={cn(
								"rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-2",
								compact ? "text-xs" : "text-sm",
							)}
						>
							<DidNarrative
								text={run.did}
								activityReferences={activityReferences}
								onReferencePreview={onActivityReferencePreview}
								onReferenceActivate={
									onActivityReferenceActivate
								}
								compact={compact}
							/>
						</div>
					</Section>
				) : fallbackActions.length > 0 ? (
					// No `did` summary at all (pre-summary or summary failed)
					// — preserve visibility with human-readable action names.
					<Section
						icon={<Wrench size={13} />}
						iconClassName="bg-blue-500/15 text-blue-600 dark:text-blue-400"
						label={`What the agent did · ${fallbackActions.length} action${fallbackActions.length === 1 ? "" : "s"}`}
						compact={compact}
					>
						<div className="grid gap-1.5">
							{visibleActions.map((item) => (
								<ActivityFallbackRow
									key={item.id}
									item={item}
								/>
							))}
							{overflow > 0 ? (
								<div className="text-xs text-muted-foreground">
									+{overflow} more —{" "}
									<Link
										to={`/agents/${run.agent_id}/runs/${run.id}`}
										state={
											runNavigationOrigin
												? createAgentRunNavigationState(
														runNavigationOrigin,
													)
												: undefined
										}
										className="text-primary hover:underline"
									>
										open full detail
									</Link>
								</div>
							) : null}
						</div>
					</Section>
				) : null}

				{run.status === "completed" ? (
					<Section
						icon={<Bot size={13} />}
						iconClassName="bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
						label="What the agent answered"
						compact={compact}
					>
						<div
							className={cn(
								"rounded-md bg-muted/50 ring-1 ring-foreground/5 px-3 py-2 whitespace-pre-wrap break-words",
								compact ? "text-xs" : "text-sm",
							)}
						>
							{run.answered ? (
								run.answered
							) : run.did ? (
								// Older summaries have no separate answer and
								// may still contain executor markers. Reuse the
								// safe narrative renderer for that fallback.
								<DidNarrative
									text={run.did}
									activityReferences={activityReferences}
									onReferencePreview={
										onActivityReferencePreview
									}
									onReferenceActivate={
										onActivityReferenceActivate
									}
									compact={compact}
								/>
							) : (
								<SummaryPlaceholder
									status={run.summary_status}
									runStatus={run.status}
								/>
							)}
						</div>
					</Section>
				) : (
					<Section
						icon={<AlertCircle size={13} />}
						iconClassName="bg-rose-500/15 text-rose-600 dark:text-rose-400"
						label={
							run.status === "budget_exceeded"
								? "Budget exceeded"
								: "Run failed"
						}
						compact={compact}
					>
						<div
							className={cn(
								"rounded-md bg-rose-500/10 px-3 py-2 whitespace-pre-wrap break-words",
								compact ? "text-xs" : "text-sm",
							)}
						>
							{run.error ?? "No error message captured."}
						</div>
					</Section>
				)}

				{run.metadata && Object.keys(run.metadata).length > 0 ? (
					<Section label="Captured data" compact={compact} plain>
						<MetadataChips metadata={run.metadata} />
					</Section>
				) : null}

				{run.summary_status === "failed" && run.summary_error ? (
					<Section label="Summary error" compact={compact} plain>
						<div
							className={cn(
								"rounded-md bg-rose-500/10 ring-1 ring-rose-500/30 px-3 py-2 text-rose-700 dark:text-rose-300 whitespace-pre-wrap break-words",
								compact ? "text-xs" : "text-sm",
							)}
						>
							{run.summary_error}
						</div>
					</Section>
				) : null}
			</div>

			{canVerdict ? (
				<div
					className={cn(
						"flex items-center gap-2 bg-muted/40",
						variant === "drawer"
							? "border-t px-4 py-3 flex-col items-stretch"
							: "mx-5 mb-5 rounded-md ring-1 ring-foreground/5 px-3 py-2.5",
						compact && "flex-col items-stretch gap-2",
					)}
					data-slot="verdict-bar"
				>
					<div className="flex items-center gap-2">
						<div className="text-sm text-muted-foreground">
							Verdict
						</div>
						<button
							type="button"
							aria-label="Mark as good"
							aria-pressed={verdict === "up"}
							onClick={() =>
								onVerdict(verdict === "up" ? null : "up")
							}
							className={cn(
								"inline-flex items-center gap-1.5 rounded-2xl border px-2.5 py-1 text-xs font-medium transition-colors",
								verdict === "up"
									? "border-emerald-500 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
									: "bg-background hover:bg-accent",
							)}
						>
							<ThumbsUp size={14} /> Good
						</button>
						<button
							type="button"
							aria-label="Mark as wrong"
							aria-pressed={verdict === "down"}
							onClick={() =>
								onVerdict(verdict === "down" ? null : "down")
							}
							className={cn(
								"inline-flex items-center gap-1.5 rounded-2xl border px-2.5 py-1 text-xs font-medium transition-colors",
								verdict === "down"
									? "border-rose-500 bg-rose-500/15 text-rose-700 dark:text-rose-300"
									: "bg-background hover:bg-accent",
							)}
						>
							<ThumbsDown size={14} /> Wrong
						</button>
					</div>
					<div
						className={cn(
							"flex flex-1",
							compact ? "ml-0" : "ml-4 max-w-[500px]",
						)}
					>
						<Input
							type="text"
							placeholder={
								verdict === "down"
									? "What should it have done?"
									: "Add a note (optional)"
							}
							value={note}
							onChange={(e) => onNote(e.target.value)}
						/>
					</div>
				</div>
			) : null}
		</div>
	);
}

interface SectionProps {
	icon?: ReactNode;
	iconClassName?: string;
	label: string;
	children: ReactNode;
	compact?: boolean;
	plain?: boolean;
}

function Section({
	icon,
	iconClassName,
	label,
	children,
	compact,
	plain,
}: SectionProps) {
	return (
		<section>
			<div className="mb-2 flex items-center gap-2">
				{icon && !plain ? (
					<div
						className={cn(
							"grid place-items-center rounded-full",
							compact ? "h-[22px] w-[22px]" : "h-7 w-7",
							iconClassName,
						)}
					>
						{icon}
					</div>
				) : null}
				<div
					className={cn(
						"font-medium",
						compact ? "text-xs" : "text-[13px]",
						plain
							? "text-[11px] uppercase tracking-wider text-muted-foreground"
							: "text-foreground",
					)}
				>
					{label}
				</div>
			</div>
			{children}
		</section>
	);
}

interface RawDisclosureProps {
	label: string;
	value: unknown;
	compact?: boolean;
}

/** Collapsible input/output block. Structured values use the shared variable tree. */
function RawDisclosure({ label, value, compact }: RawDisclosureProps) {
	const [open, setOpen] = useState(false);
	const contentId = useId();
	const serialized = payloadText(value);
	const structuredValue = parseStructuredPayload(value);
	return (
		<div className="overflow-hidden rounded-md bg-muted/50 ring-1 ring-foreground/5">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				aria-controls={contentId}
				className={cn(
					"flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-muted-foreground hover:text-foreground",
					compact ? "text-xs" : "text-[13px]",
				)}
			>
				<ChevronRight
					className={cn(
						"h-3 w-3 transition-transform",
						open && "rotate-90",
					)}
				/>
				<span>{label}</span>
				<span className="ml-auto text-[11px]">
					{serialized.length.toLocaleString()} chars
				</span>
			</button>
			{open ? (
				<div
					id={contentId}
					className={cn(
						"max-h-[240px] overflow-auto border-t bg-muted px-3 py-2 whitespace-pre-wrap break-words",
						compact ? "text-[11px]" : "text-xs",
					)}
				>
					{structuredValue !== UNPARSEABLE_PAYLOAD ? (
						<VariablesTreeView
							data={asPayloadVariables(structuredValue)}
						/>
					) : (
						serialized
					)}
				</div>
			) : null}
		</div>
	);
}

const UNPARSEABLE_PAYLOAD = Symbol("unparseable-payload");

function parseStructuredPayload(value: unknown): unknown {
	if (value !== null && typeof value === "object") return value;
	if (typeof value !== "string") return UNPARSEABLE_PAYLOAD;
	const trimmed = value.trim();
	if (!(
		(trimmed.startsWith("{") && trimmed.endsWith("}")) ||
		(trimmed.startsWith("[") && trimmed.endsWith("]"))
	)) {
		return UNPARSEABLE_PAYLOAD;
	}
	try {
		return JSON.parse(trimmed);
	} catch {
		return UNPARSEABLE_PAYLOAD;
	}
}

function asPayloadVariables(value: unknown): Record<string, unknown> {
	if (value !== null && typeof value === "object" && !Array.isArray(value)) {
		return value as Record<string, unknown>;
	}
	return { value };
}

export interface MetadataChipsProps {
	metadata: Record<string, string>;
	highlight?: string;
}

export function MetadataChips({ metadata, highlight }: MetadataChipsProps) {
	const entries = Object.entries(metadata);
	if (!entries.length) return null;
	const q = highlight?.trim().toLowerCase() ?? "";
	return (
		<div className="flex flex-wrap gap-1.5">
			{entries.map(([k, v]) => {
				const isHit =
					q &&
					(k.toLowerCase().includes(q) ||
						v.toLowerCase().includes(q));
				return (
					<span
						key={k}
						title={`${k}=${v}`}
						className={cn(
							"inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]",
							isHit
								? "border-transparent bg-yellow-500/15 text-yellow-700 dark:text-yellow-300"
								: "border-border bg-card text-foreground",
						)}
					>
						<span className="text-muted-foreground">{k}</span>
						<span className="font-mono">{v}</span>
					</span>
				);
			})}
		</div>
	);
}
