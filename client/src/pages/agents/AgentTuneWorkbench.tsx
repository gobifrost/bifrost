/**
 * AgentTuneWorkbench — two-column tuning workbench.
 *
 * Left: flagged runs (expandable transcripts) + Generate proposal CTA.
 * Right: prompt editor (current collapsible, proposed editable with diff).
 *
 * Top-right header action: Run dry-run (enabled once a proposal exists).
 * Dry-run results, when present, render as a full-width panel below the
 * header.
 */

import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Loader2, PlayCircle, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import { FlaggedRunCard } from "@/components/agents/FlaggedRunCard";
import { PromptDiffViewer } from "@/components/agents/PromptDiffViewer";
import { TuneHeader } from "@/components/agents/TuneHeader";
import {
	TONE_MUTED,
	TYPE_MUTED,
	TYPE_PANE_LABEL,
} from "@/components/agents/design-tokens";

import { useAgent } from "@/hooks/useAgents";
import { useAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	useApplyTuning,
	useTuningDryRun,
	useTuningSession,
	type ConsolidatedDryRunResponse,
	type ConsolidatedProposal,
} from "@/services/agentTuning";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export function AgentTuneWorkbench() {
	const { id: agentId } = useParams<{ id: string }>();

	const { data: agent } = useAgent(agentId);
	const { data: stats, isLoading: statsLoading } = useAgentStats(agentId);
	const { data: flaggedResp, isLoading: flaggedLoading } = useAgentRuns({
		agentId,
		verdict: "down",
	});

	const tuningSession = useTuningSession();
	const applyTuning = useApplyTuning();
	const tuningDryRun = useTuningDryRun();
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const flagged: AgentRun[] = (flaggedResp?.items ?? []) as AgentRun[];
	const hasFlaggedRuns = flagged.length > 0;
	const canGenerate = hasFlaggedRuns && !tuningSession.isPending;

	const [proposal, setProposal] = useState<ConsolidatedProposal | null>(null);
	const [edits, setEdits] = useState<string>("");
	const [currentOpen, setCurrentOpen] = useState(false);
	const [dryRun, setDryRun] = useState<ConsolidatedDryRunResponse | null>(null);

	const currentPrompt = agent?.system_prompt ?? "";

	function handleGenerate() {
		if (!agentId) return;
		tuningSession.mutate(
			{ params: { path: { agent_id: agentId } } },
			{
				onSuccess: (data) => {
					setProposal(data);
					setEdits(data.proposed_prompt);
				},
				onError: () => toast.error("Failed to generate proposal"),
			},
		);
	}

	function handleDiscard() {
		setProposal(null);
		setEdits("");
		setDryRun(null);
	}

	function handleDryRun() {
		if (!agentId || !edits.trim()) return;
		tuningDryRun.mutate(
			{
				params: { path: { agent_id: agentId } },
				body: { proposed_prompt: edits },
			},
			{
				onSuccess: (data) => {
					setDryRun(data);
				},
				onError: () => toast.error("Dry-run failed"),
			},
		);
	}

	function handleApply() {
		if (!agentId) return;
		applyTuning.mutate(
			{
				params: { path: { agent_id: agentId } },
				body: { new_prompt: edits },
			},
			{
				onSuccess: () => {
					toast.success("Prompt updated");
					queryClient.invalidateQueries({
						queryKey: ["get", "/api/agents"],
					});
					queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
					navigate(`/agents/${agentId}`);
				},
				onError: () => toast.error("Failed to apply tuning"),
			},
		);
	}

	return (
		<div
			className="mx-auto flex max-w-[1400px] flex-col gap-6 p-6 lg:p-8"
			data-testid="agent-tune-workbench"
		>
			<TuneHeader
				agentId={agentId}
				agentName={agent?.name}
				flaggedCount={flagged.length}
				stats={stats ?? null}
				statsLoading={statsLoading}
				action={
					<Button
						type="button"
						variant="outline"
						size="sm"
						data-testid="dryrun-button"
						disabled={
							!proposal || !edits.trim() || tuningDryRun.isPending
						}
						onClick={handleDryRun}
					>
						{tuningDryRun.isPending ? (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						) : (
							<PlayCircle className="h-3.5 w-3.5" />
						)}
						Run dry-run
					</Button>
				}
			/>

			{/* Dry-run results (full-width, appears after first run) */}
			{dryRun ? (
				<div
					className="flex flex-col gap-2 rounded-2xl bg-card p-4 shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10"
					data-testid="dryrun-results"
				>
					<div className="flex items-center justify-between">
						<div className={TYPE_PANE_LABEL}>Dry-run results</div>
						{(() => {
							const total = dryRun.results.length;
							const wouldChange = dryRun.results.filter(
								(r) => !r.would_still_decide_same,
							).length;
							return (
								<span className={cn("text-xs", TONE_MUTED)}>
									{wouldChange} of {total} would change behavior
								</span>
							);
						})()}
					</div>
					<div className="grid grid-cols-1 gap-2 md:grid-cols-2">
						{dryRun.results.map((r) => (
							<div
								key={r.run_id}
								className="rounded-md bg-muted/50 p-2 text-xs ring-1 ring-foreground/5"
							>
								<div className="flex items-center justify-between gap-2">
									<span className="font-mono text-[11px] text-muted-foreground">
										{r.run_id.slice(0, 8)}…
									</span>
									<Badge
										variant="outline"
										className={cn(
											"text-[10.5px]",
											r.would_still_decide_same
												? "border-yellow-500/40 text-yellow-500"
												: "border-emerald-500/40 text-emerald-500",
										)}
									>
										{r.would_still_decide_same
											? "Still wrong"
											: "Would change"}
									</Badge>
								</div>
								<div className={cn("mt-1", TONE_MUTED)}>
									{r.reasoning}
								</div>
								<div className="mt-0.5 text-[10.5px] text-muted-foreground">
									confidence: {Math.round(r.confidence * 100)}%
								</div>
							</div>
						))}
					</div>
				</div>
			) : null}

			<div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
				{/* Left: flagged runs */}
				<div className="flex flex-col gap-3" data-testid="tune-pane-flagged">
					<div className={TYPE_PANE_LABEL}>Flagged runs ({flagged.length})</div>
					{flaggedLoading ? (
						<Skeleton className="h-24 w-full" />
					) : !hasFlaggedRuns ? (
						<div
							className={cn(
								"rounded-2xl border border-dashed p-4 text-center",
								TYPE_MUTED,
								TONE_MUTED,
							)}
						>
							No flagged runs. Mark a run thumbs-down from the runs tab to tune
							against it.
						</div>
					) : (
						<div className="flex flex-col gap-2">
							{flagged.map((r) => (
								<FlaggedRunCard key={r.id} run={r} />
							))}
						</div>
					)}
					<Button
						type="button"
						data-testid="generate-proposal-button"
						disabled={!canGenerate}
						onClick={handleGenerate}
					>
						{tuningSession.isPending ? (
							<Loader2 className="h-3.5 w-3.5 animate-spin" />
						) : (
							<Sparkles className="h-3.5 w-3.5" />
						)}
						{proposal
							? "Re-generate"
							: `Generate proposal from ${flagged.length} run${flagged.length === 1 ? "" : "s"}`}
					</Button>
				</div>

				{/* Center: prompt editor */}
				<div className="flex flex-col gap-3" data-testid="tune-pane-editor">
					<div className={TYPE_PANE_LABEL}>Prompt editor</div>

					{/* Current prompt (collapsible) */}
					<div className="overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10">
						<button
							type="button"
							data-testid="current-prompt-toggle"
							onClick={() => setCurrentOpen((o) => !o)}
							className="flex w-full items-center justify-between px-3 py-2 text-left text-xs"
							aria-expanded={currentOpen}
						>
							<span className="font-medium">Current prompt</span>
							<ChevronDown
								className={cn(
									"h-3 w-3 transition-transform",
									currentOpen ? "rotate-0" : "-rotate-90",
								)}
							/>
						</button>
						{currentOpen ? (
							<pre className="max-h-60 overflow-y-auto whitespace-pre-wrap border-t px-3 py-2 font-mono text-[11.5px] text-muted-foreground">
								{currentPrompt || "(no system prompt set)"}
							</pre>
						) : null}
					</div>

					{/* Proposed prompt */}
					{tuningSession.isPending ? (
						<div className="rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10 p-4">
							<div className={cn("mb-2 text-xs", TONE_MUTED)}>
								Building proposal…
							</div>
							<Skeleton className="h-32 w-full" />
						</div>
					) : !proposal ? (
						<div
							data-testid="editor-empty-state"
							className={cn(
								"rounded-2xl border border-dashed p-6 text-center",
								TYPE_MUTED,
								TONE_MUTED,
							)}
						>
							Click <span className="font-medium">Generate proposal</span> in
							the left pane to have the tuner read the flagged runs and
							suggest one consolidated prompt change.
						</div>
					) : (
						<div className="flex flex-col gap-3">
							<div className="overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10">
								<div className="border-b px-3 py-2 text-xs font-medium">
									Proposed prompt (editable)
								</div>
								<Textarea
									data-testid="proposal-textarea"
									value={edits}
									onChange={(e) => setEdits(e.target.value)}
									rows={12}
									className="h-72 max-h-72 resize-y overflow-y-auto rounded-none border-0 font-mono text-[12px] [field-sizing:fixed] focus-visible:ring-0"
								/>
							</div>
							{proposal.summary ? (
								<p className={cn("italic", TYPE_MUTED, TONE_MUTED)}>
									{proposal.summary}
								</p>
							) : null}
							<PromptDiffViewer before={currentPrompt} after={edits} />
							<div className="flex items-center justify-end gap-2">
								<Button
									type="button"
									variant="outline"
									size="sm"
									data-testid="discard-button"
									onClick={handleDiscard}
								>
									<X className="h-3.5 w-3.5" />
									Discard
								</Button>
								<Button
									type="button"
									size="sm"
									data-testid="apply-button"
									disabled={applyTuning.isPending || !edits.trim()}
									onClick={handleApply}
								>
									{applyTuning.isPending ? (
										<Loader2 className="h-3.5 w-3.5 animate-spin" />
									) : (
										<Check className="h-3.5 w-3.5" />
									)}
									Apply live
								</Button>
							</div>
						</div>
					)}
				</div>

			</div>
		</div>
	);
}

export default AgentTuneWorkbench;
