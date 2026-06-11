/**
 * AgentRunDetailPage — full-page detail view for a single agent run.
 *
 * Routes:
 *   /agents/:agentId/runs/:runId  → this page
 *
 * Layout:
 *   - Page header (breadcrumb back to agent + run summary + status badge)
 *   - Main column: <RunReviewPanel variant="page"> + Advanced collapsible
 *     for the raw step timeline
 *   - Sidebar: run metadata, AI usage cost breakdown, regen-summary button
 *     (admins only when summary failed), and per-flag conversation when the
 *     run's verdict is "down"
 *
 * Replaces (T33) the legacy `client/src/pages/AgentRunDetail.tsx`.
 *
 * Ported from /tmp/agent-mockup/src/pages/RunDetailPage.tsx — uses real
 * hooks, shadcn primitives, Tailwind. No inline styles.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
	AlertCircle,
	ArrowLeft,
	Bot,
	CheckCircle,
	ChevronDown,
	Clock,
	Loader2,
	RefreshCw,
	Sparkles,
	Terminal,
	XCircle,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/contexts/AuthContext";
import { useAgent } from "@/hooks/useAgents";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import {
	formatCost,
	formatDuration,
	formatNumber,
} from "@/lib/utils";
import {
	useAgentRun,
	useClearVerdict,
	useFlagConversation,
	useRegenerateSummary,
	useRerunAgentRun,
	useSendFlagMessage,
	useSetVerdict,
} from "@/services/agentRuns";
import type { components } from "@/lib/v1";

import { FlagConversation } from "@/components/agents/FlagConversation";
import {
	RunReviewPanel,
	type Verdict,
} from "@/components/agents/RunReviewPanel";
import { Timeline } from "@/components/agents/Timeline";

type AgentRunDetailResponse = components["schemas"]["AgentRunDetailResponse"];

export function AgentRunDetailPage() {
	const { agentId, runId } = useParams<{
		agentId: string;
		runId: string;
	}>();
	const queryClient = useQueryClient();
	const { isPlatformAdmin } = useAuth();

	// `useAgentRun` returns a hand-rolled `AgentRunDetail` type that predates
	// some OpenAPI fields (asked/did/verdict/etc). Re-cast to the OpenAPI
	// schema for full field access.
	const { data: rawRun, isLoading } = useAgentRun(runId);
	const run = rawRun as unknown as AgentRunDetailResponse | undefined;
	const { data: agent } = useAgent(agentId);

	// Refetch this run whenever the backend broadcasts an update for it —
	// covers summarizer transitions (pending → generating → completed) and
	// step-writes so the page reflects live state without a manual refresh.
	useAgentRunUpdates({ agentId });

	const verdict = ((run?.verdict as Verdict | undefined) ?? null) as Verdict;
	const [note, setNote] = useState<string>(run?.verdict_note ?? "");
	const [advancedOpen, setAdvancedOpen] = useState(false);

	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	const regenSummary = useRegenerateSummary();
	const rerun = useRerunAgentRun();
	const navigate = useNavigate();

	const isFlagged = verdict === "down";
	const { data: conversation } = useFlagConversation(
		isFlagged ? runId : undefined,
	);
	const sendMessage = useSendFlagMessage();

	function invalidateRun() {
		queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
		queryClient.invalidateQueries({ queryKey: ["agent-runs", runId] });
	}

	function handleVerdict(next: Verdict) {
		if (!runId) return;
		if (next === null) {
			clearVerdict.mutate(
				{ params: { path: { run_id: runId } } },
				{ onSuccess: invalidateRun },
			);
		} else {
			setVerdict.mutate(
				{
					params: { path: { run_id: runId } },
					body: { verdict: next },
				},
				{ onSuccess: invalidateRun },
			);
		}
	}

	function handleSendChat(text: string) {
		if (!runId) return;
		sendMessage.mutate({
			params: { path: { run_id: runId } },
			body: { content: text },
		});
	}

	function handleRegenerate() {
		if (!runId) return;
		regenSummary.mutate(
			{ params: { path: { run_id: runId } } },
			{
				onSuccess: () => {
					toast.success("Summary regeneration queued");
					invalidateRun();
				},
				onError: () => {
					toast.error("Failed to regenerate summary");
				},
			},
		);
	}

	function handleRerun() {
		if (!runId || !run?.agent_id) return;
		rerun.mutate(
			{ params: { path: { run_id: runId } } },
			{
				onSuccess: (data) => {
					toast.success("Rerun queued");
					if (data.run_id) {
						navigate(`/agents/${run.agent_id}/runs/${data.run_id}`);
					}
				},
				onError: () => toast.error("Failed to queue rerun"),
			},
		);
	}

	if (isLoading) {
		return (
			<div className="flex flex-col gap-5 max-w-7xl mx-auto">
				<Skeleton className="h-6 w-32" />
				<Skeleton className="h-12 w-1/2" />
				<div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
					<div className="lg:col-span-2 space-y-4">
						<Skeleton className="h-64 w-full" />
					</div>
					<div className="space-y-4">
						<Skeleton className="h-48 w-full" />
					</div>
				</div>
			</div>
		);
	}

	if (!run) {
		return (
			<div
				className="flex flex-col items-center justify-center gap-3 py-16 text-center"
				data-testid="run-not-found"
			>
				<AlertCircle className="h-10 w-10 text-muted-foreground" />
				<div className="text-lg font-medium">Run not found</div>
				<Button asChild variant="outline">
					<Link to={agentId ? `/agents/${agentId}` : "/agents"}>
						Back to agent
					</Link>
				</Button>
			</div>
		);
	}

	const summaryStatus =
		(run as unknown as { summary_status?: string }).summary_status ?? null;
	const summaryFailed = summaryStatus === "failed";
	const summaryInFlight =
		summaryStatus === "pending" || summaryStatus === "generating";
	// While the summarizer is running, the RunReviewPanel already shows a
	// status banner with a regenerate button — hide the sidebar card so we
	// don't render two competing affordances.
	const showRegen = (isPlatformAdmin || summaryFailed) && !summaryInFlight;
	// Header title: `asked` is the user-facing TL;DR (capped ~100 chars by
	// the summarizer prompt). `did` is a multi-sentence narrative under v3+
	// and too long for a title; only fall back to it when `asked` is empty.
	const headerSummary = run.asked || run.did || "Agent run";

	return (
		<div
			className="flex flex-col gap-5 max-w-7xl mx-auto"
			data-testid="agent-run-detail-page"
		>
			{/* Breadcrumb */}
			<Link
				to={`/agents/${run.agent_id}`}
				className="inline-flex w-fit items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
			>
				<ArrowLeft className="h-3 w-3" />
				{agent?.name ?? "Back to agent"}
			</Link>

			{/* Header */}
			<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
				<div className="flex min-w-0 items-start gap-3">
					<Bot className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" />
					<div className="min-w-0">
						<h1 className="truncate text-2xl font-extrabold tracking-tight">
							{headerSummary}
						</h1>
						<div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
							<RunStatusBadge status={run.status} />
							{run.started_at ? (
								<span className="inline-flex items-center gap-1">
									<Clock className="h-3 w-3" />
									{new Date(run.started_at).toLocaleString()}
								</span>
							) : null}
							{run.duration_ms != null ? (
								<>
									<span>·</span>
									<span>
										{formatDuration(run.duration_ms)}
									</span>
								</>
							) : null}
							<span>·</span>
							<span>
								{run.iterations_used} iter ·{" "}
								{formatNumber(run.tokens_used)} tok
							</span>
						</div>
					</div>
				</div>
				<Button
					type="button"
					variant="outline"
					size="sm"
					data-testid="rerun-button"
					disabled={rerun.isPending}
					onClick={handleRerun}
				>
					{rerun.isPending ? (
						<Loader2 className="h-3.5 w-3.5 animate-spin" />
					) : (
						<RefreshCw className="h-3.5 w-3.5" />
					)}
					Rerun
				</Button>
			</div>

			{/* Two-column layout */}
			<div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
				{/* Main column. ``min-w-0`` is essential — without it long
				    JSON strings inside the panel push past the column width
				    and into the sidebar. */}
				<div className="lg:col-span-2 flex min-w-0 flex-col gap-4">
					<Card className="min-w-0 overflow-hidden">
						<RunReviewPanel
							run={run}
							variant="page"
							verdict={verdict}
							note={note}
							onVerdict={handleVerdict}
							onNote={setNote}
						/>
					</Card>

					{/* Advanced (raw step timeline) */}
					<Collapsible
						open={advancedOpen}
						onOpenChange={setAdvancedOpen}
					>
						<CollapsibleTrigger asChild>
							<button
								type="button"
								className="flex w-full items-center gap-2 rounded-2xl bg-card px-3 py-2 text-left text-xs text-muted-foreground shadow-sm ring-1 ring-foreground/5 hover:bg-accent/40 dark:ring-foreground/10"
							>
								<ChevronDown
									className={`h-3 w-3 transition-transform ${
										advancedOpen ? "rotate-0" : "-rotate-90"
									}`}
								/>
								<Terminal className="h-3 w-3" />
								<span>
									Timeline ({(run.steps ?? []).length} steps)
								</span>
								<span className="ml-2 text-[11px]">
									Step-by-step view of what the executor did
								</span>
							</button>
						</CollapsibleTrigger>
						<CollapsibleContent>
							<Card className="mt-2">
								<CardContent className="p-3">
									<Timeline steps={run.steps ?? []} />
								</CardContent>
							</Card>
						</CollapsibleContent>
					</Collapsible>

					{/* Per-flag conversation (only when verdict=down) */}
					{isFlagged ? (
						<Card data-testid="flag-conversation-card">
							<CardHeader className="pb-2">
								<CardTitle className="flex items-center gap-2 text-sm">
									<Sparkles className="h-4 w-4" />
									Tuning conversation
								</CardTitle>
							</CardHeader>
							<CardContent className="p-0">
								<div className="flex h-[420px] flex-col">
									<FlagConversation
										conversation={conversation ?? null}
										onSend={handleSendChat}
										pending={sendMessage.isPending}
									/>
								</div>
							</CardContent>
						</Card>
					) : null}
				</div>

				{/* Sidebar */}
				<div className="flex min-w-0 flex-col gap-4">
					<Card>
						<CardHeader className="pb-2">
							<CardTitle className="text-sm">
								Run metadata
							</CardTitle>
						</CardHeader>
						<CardContent>
							<dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
								<MetaRow label="Run ID">
									<span className="font-mono text-[11px] break-all">
										{run.id}
									</span>
								</MetaRow>
								{run.started_at ? (
									<MetaRow label="Started">
										{new Date(
											run.started_at,
										).toLocaleString()}
									</MetaRow>
								) : null}
								<MetaRow label="Duration">
									{run.duration_ms != null
										? formatDuration(run.duration_ms)
										: "—"}
								</MetaRow>
								<MetaRow label="Iterations">
									{run.iterations_used}
								</MetaRow>
								<MetaRow label="Tokens">
									{formatNumber(run.tokens_used)}
								</MetaRow>
								<MetaRow label="Model">
									<span className="font-mono">
										{run.llm_model ?? "default"}
									</span>
								</MetaRow>
								<MetaRow label="Trigger">
									{run.trigger_type}
								</MetaRow>
								{run.caller_email ? (
									<MetaRow label="Caller">
										{run.caller_name ?? run.caller_email}
									</MetaRow>
								) : null}
							</dl>
						</CardContent>
					</Card>

					{/* AI usage */}
					{run.ai_usage && run.ai_usage.length > 0 ? (
						<AIUsageCard
							usage={run.ai_usage}
							totals={run.ai_totals ?? null}
						/>
					) : null}

					{/* Regenerate summary (admin-only / failed summary) */}
					{showRegen ? (
						<Card>
							<CardContent className="flex items-center justify-between gap-3 py-3 text-xs">
								<div>
									<div className="font-medium">
										Summary
									</div>
									<div className="text-muted-foreground">
										{summaryFailed
											? "Generation failed"
											: "Re-run the summarizer"}
									</div>
								</div>
								<Button
									size="sm"
									variant="outline"
									disabled={regenSummary.isPending}
									onClick={handleRegenerate}
									data-testid="regen-summary-button"
								>
									{regenSummary.isPending ? (
										<Loader2 className="h-3 w-3 animate-spin" />
									) : (
										<RefreshCw className="h-3 w-3" />
									)}
									Regenerate
								</Button>
							</CardContent>
						</Card>
					) : null}

					{/* Agent card */}
					<Card>
						<CardHeader className="pb-2">
							<CardTitle className="text-sm">Agent</CardTitle>
						</CardHeader>
						<CardContent>
							<Link
								to={`/agents/${run.agent_id}`}
								className="flex items-start gap-2 text-sm hover:underline"
							>
								<Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
								<div className="min-w-0">
									<div className="truncate font-medium">
										{agent?.name ??
											run.agent_name ??
											"Agent"}
									</div>
									{agent?.description ? (
										<div className="text-xs text-muted-foreground line-clamp-2">
											{agent.description}
										</div>
									) : null}
								</div>
							</Link>
						</CardContent>
					</Card>
				</div>
			</div>
		</div>
	);
}

function MetaRow({
	label,
	children,
}: {
	label: string;
	children: React.ReactNode;
}) {
	return (
		<>
			<dt className="text-muted-foreground">{label}</dt>
			<dd className="text-right text-foreground">{children}</dd>
		</>
	);
}

function RunStatusBadge({ status }: { status: string }) {
	switch (status) {
		case "completed":
			return (
				<Badge variant="default" className="bg-emerald-500 text-white">
					<CheckCircle className="h-3 w-3" /> Completed
				</Badge>
			);
		case "failed":
			return (
				<Badge variant="destructive">
					<XCircle className="h-3 w-3" /> Failed
				</Badge>
			);
		case "running":
			return (
				<Badge variant="secondary">
					<Loader2 className="h-3 w-3 animate-spin" /> Running
				</Badge>
			);
		case "budget_exceeded":
			return (
				<Badge variant="warning">
					<AlertCircle className="h-3 w-3" /> Budget exceeded
				</Badge>
			);
		default:
			return <Badge variant="outline">{status}</Badge>;
	}
}

interface AIUsageEntry {
	provider: string;
	model: string;
	input_tokens: number;
	output_tokens: number;
	cost?: string | null;
}

interface AIUsageTotals {
	total_input_tokens: number;
	total_output_tokens: number;
	total_cost: string;
	call_count: number;
}

function AIUsageCard({
	usage,
	totals,
}: {
	usage: NonNullable<AgentRunDetailResponse["ai_usage"]>;
	totals: AgentRunDetailResponse["ai_totals"] | null;
}) {
	const grouped = useMemo(() => {
		const map = new Map<
			string,
			{
				model: string;
				calls: number;
				input_tokens: number;
				output_tokens: number;
				cost: number;
			}
		>();
		for (const u of usage as AIUsageEntry[]) {
			const cost = u.cost ? parseFloat(String(u.cost)) || 0 : 0;
			const existing = map.get(u.model);
			if (existing) {
				existing.calls += 1;
				existing.input_tokens += u.input_tokens;
				existing.output_tokens += u.output_tokens;
				existing.cost += cost;
			} else {
				map.set(u.model, {
					model: u.model,
					calls: 1,
					input_tokens: u.input_tokens,
					output_tokens: u.output_tokens,
					cost,
				});
			}
		}
		return Array.from(map.values());
	}, [usage]);

	const totalsTyped = totals as AIUsageTotals | null;

	return (
		<Card data-testid="ai-usage-card">
			<CardHeader className="pb-2">
				<CardTitle className="flex items-center gap-2 text-sm">
					<Sparkles className="h-4 w-4 text-purple-500" />
					AI usage
				</CardTitle>
			</CardHeader>
			<CardContent className="overflow-x-auto">
				<table className="w-full text-xs">
					<thead>
						<tr className="border-b">
							<th className="py-1.5 pr-2 text-left font-medium text-muted-foreground">
								Model
							</th>
							<th className="py-1.5 pr-2 text-right font-medium text-muted-foreground">
								Calls
							</th>
							<th className="py-1.5 pr-2 text-right font-medium text-muted-foreground">
								In
							</th>
							<th className="py-1.5 pr-2 text-right font-medium text-muted-foreground">
								Out
							</th>
							<th className="py-1.5 text-right font-medium text-muted-foreground">
								Cost
							</th>
						</tr>
					</thead>
					<tbody>
						{grouped.map((row) => (
							<tr key={row.model} className="border-b last:border-0">
								<td className="py-1.5 pr-2 font-mono text-muted-foreground">
									{row.model.length > 20
										? `${row.model.slice(0, 18)}…`
										: row.model}
								</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{row.calls}
								</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{formatNumber(row.input_tokens)}
								</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{formatNumber(row.output_tokens)}
								</td>
								<td className="py-1.5 text-right font-mono">
									{formatCost(row.cost)}
								</td>
							</tr>
						))}
					</tbody>
					{totalsTyped ? (
						<tfoot>
							<tr className="bg-muted/40 font-medium">
								<td className="py-1.5 pr-2">Total</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{totalsTyped.call_count}
								</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{formatNumber(
										totalsTyped.total_input_tokens,
									)}
								</td>
								<td className="py-1.5 pr-2 text-right font-mono">
									{formatNumber(
										totalsTyped.total_output_tokens,
									)}
								</td>
								<td className="py-1.5 text-right font-mono">
									{formatCost(totalsTyped.total_cost)}
								</td>
							</tr>
						</tfoot>
					) : null}
				</table>
			</CardContent>
		</Card>
	);
}

export default AgentRunDetailPage;
