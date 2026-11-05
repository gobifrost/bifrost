import { PageWorkspace } from "@/components/layout/PageWorkspace";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetDescription,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
/** Full-width prompt review with supporting evidence in an overlay sheet. */

import { FleetReadError } from "./FleetReadError";
import { PageLoader } from "@/components/PageLoader";
import { useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, PlayCircle, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { TuningActionError } from "./TuningActionError";
import { TuningDryRunResults } from "./TuningDryRunResults";
import { Button } from "@/components/ui/button";
import { TuningProposalEditor } from "./TuningProposalEditor";

import { TuningFlaggedRuns } from "./TuningFlaggedRuns";
import { TuneHeader } from "@/components/agents/TuneHeader";

import { useAgent } from "@/hooks/useAgents";
import { useInfiniteAgentRuns } from "@/services/agentRuns";
import { useAgentStats } from "@/services/agents";
import {
	useApplyTuning,
	useTuningDryRun,
	useTuningSession,
	type ConsolidatedDryRunResponse,
	type ConsolidatedProposal,
} from "@/services/agentTuning";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

export function AgentTuneWorkbench() {
	const { id: agentId } = useParams<{ id: string }>();

	const {
		data: agent,
		isLoading: agentLoading,
		isError: agentError,
		isFetching: agentFetching,
		refetch: refetchAgent,
	} = useAgent(agentId);
	const {
		data: stats,
		isLoading: statsLoading,
		isError: statsError,
		isFetching: statsFetching,
		refetch: refetchStats,
	} = useAgentStats(agentId);
	const {
		data: flaggedResp,
		isLoading: flaggedLoading,
		isError: flaggedReadError,
		isFetching: flaggedFetching,
		refetch: refetchFlagged,
		hasNextPage,
		fetchNextPage,
		isFetchingNextPage,
		isFetchNextPageError,
	} = useInfiniteAgentRuns({
		agentId,
		verdict: "down",
	});

	const tuningSession = useTuningSession();
	const applyTuning = useApplyTuning();
	const tuningDryRun = useTuningDryRun();
	const navigate = useNavigate();
	const queryClient = useQueryClient();

	const flagged: AgentRun[] = (flaggedResp?.pages.flatMap(
		(page) => page.items,
	) ?? []) as AgentRun[];
	const flaggedTotal = flaggedResp?.pages[0]?.total ?? flagged.length;
	const flaggedError = flaggedReadError && !isFetchNextPageError;
	const hasFlaggedRuns = flagged.length > 0;
	const busy =
		tuningSession.isPending ||
		tuningDryRun.isPending ||
		applyTuning.isPending;
	const canGenerate = hasFlaggedRuns && !busy && !flaggedError && !agentError;

	const [proposal, setProposal] = useState<ConsolidatedProposal | null>(null);
	const [proposalKey, setProposalKey] = useState(0);
	const [edits, setEdits] = useState<string>("");
	const [evidence, setEvidence] = useState<
		"runs" | "evaluation" | "context" | null
	>(null);
	const [dryRun, setDryRun] = useState<ConsolidatedDryRunResponse | null>(
		null,
	);

	const [actionError, setActionError] = useState<{
		action: "generate" | "dry-run" | "apply";
		message: string;
	} | null>(null);
	const evidenceTrigger = useRef<HTMLButtonElement | null>(null);
	const currentPrompt = agent?.system_prompt ?? "";

	function handleGenerate() {
		if (!agentId || !canGenerate) return;
		setActionError(null);
		tuningSession.mutate(
			{ params: { path: { agent_id: agentId } } },
			{
				onSuccess: (data) => {
					setProposal(data);
					setProposalKey((key) => key + 1);
					setEdits(data.proposed_prompt);
					setDryRun(null);
				},
				onError: () =>
					setActionError({
						action: "generate",
						message:
							"Could not generate a proposal. Your existing edits are preserved. Try Generate proposal again.",
					}),
			},
		);
	}

	function handleDiscard() {
		if (busy) return;
		setActionError(null);
		setProposal(null);
		setEdits("");
		setDryRun(null);
	}

	function handleDryRun() {
		if (!agentId || !edits.trim() || busy) return;
		setActionError(null);
		tuningDryRun.mutate(
			{
				params: { path: { agent_id: agentId } },
				body: { proposed_prompt: edits },
			},
			{
				onSuccess: (data) => {
					setDryRun(data);
				},
				onError: () =>
					setActionError({
						action: "dry-run",
						message:
							"Could not complete the dry-run. Your proposal is preserved. Try Run dry-run again.",
					}),
			},
		);
	}

	function handleApply() {
		if (!agentId || !edits.trim() || busy) return;
		setActionError(null);
		applyTuning.mutate(
			{
				params: { path: { agent_id: agentId } },
				body: { new_prompt: edits },
			},
			{
				onSuccess: async () => {
					toast.success("Prompt updated");
					await Promise.all([
						queryClient.invalidateQueries({
							queryKey: ["get", "/api/agents"],
						}),
						queryClient.invalidateQueries({
							queryKey: [
								"get",
								"/api/agents/{agent_id}",
								{ params: { path: { agent_id: agentId } } },
							],
						}),
						queryClient.invalidateQueries({
							queryKey: ["get", "/api/agents/{agent_id}/stats"],
						}),
						queryClient.invalidateQueries({
							queryKey: ["get", "/api/agents/stats/fleet"],
						}),
						queryClient.invalidateQueries({
							queryKey: ["agent-runs"],
						}),
						queryClient.invalidateQueries({
							queryKey: ["agent-runs-infinite"],
						}),
					]);
					navigate(`/agents/${agentId}`);
				},
				onError: () =>
					setActionError({
						action: "apply",
						message:
							"Could not confirm the prompt update. Your proposal is preserved. Try Apply live again.",
					}),
			},
		);
	}

	if (!agent && agentLoading) return <PageLoader />;
	if (!agent && agentError)
		return (
			<div className="space-y-4">
				<h1 className="font-display text-2xl font-semibold">
					Tune agent
				</h1>
				<FleetReadError
					resource="agent"
					cached={false}
					pending={agentFetching}
					onRetry={() => {
						void refetchAgent();
					}}
				/>
			</div>
		);

	const generationAction = (
		<Button
			type="button"
			variant={proposal ? "outline" : "default"}
			data-testid="generate-proposal-button"
			disabled={!canGenerate}
			onClick={handleGenerate}
			className="min-h-11"
		>
			{tuningSession.isPending ? (
				<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
			) : (
				<Sparkles className="size-4" />
			)}
			{proposal
				? "Re-generate"
				: `Generate Proposal From ${flaggedTotal} Run${flaggedTotal === 1 ? "" : "s"}`}
		</Button>
	);
	const evaluationAction = (
		<Button
			type="button"
			variant="outline"
			data-testid="dryrun-button"
			disabled={!proposal || !edits.trim() || busy}
			onClick={handleDryRun}
			className="min-h-11"
		>
			{tuningDryRun.isPending ? (
				<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
			) : (
				<PlayCircle className="size-4" />
			)}
			Run Dry-Run
		</Button>
	);
	const failed =
		dryRun?.results.filter((result) => result.evaluation_failed).length ??
		0;
	const evidenceActions = (
		<div className="flex flex-wrap gap-2">
			<Button
				variant="outline"
				className="min-h-11"
				onClick={(event) => {
					evidenceTrigger.current = event.currentTarget;
					setEvidence("runs");
				}}
			>
				Flagged Runs ({flaggedTotal})
			</Button>
			<Button
				variant="outline"
				className="min-h-11"
				disabled={!dryRun}
				onClick={(event) => {
					evidenceTrigger.current = event.currentTarget;
					setEvidence("evaluation");
				}}
			>
				Evaluation
				{dryRun ? (failed ? ` · ${failed} Failed` : " · Ready") : ""}
			</Button>
			<Button
				variant="ghost"
				className="min-h-11"
				onClick={(event) => {
					evidenceTrigger.current = event.currentTarget;
					setEvidence("context");
				}}
			>
				Context
			</Button>
		</div>
	);
	return (
		<PageWorkspace
			className="min-w-0 gap-3"
			data-testid="agent-tune-workbench"
		>
			<TuneHeader
				agentId={agentId}
				agentName={agent?.name}
				flaggedCount={flaggedTotal}
				stats={stats ?? null}
				statsLoading={statsLoading}
				action={evidenceActions}
			/>
			{agentError && (
				<FleetReadError
					resource="agent"
					cached={!!agent}
					pending={agentFetching}
					onRetry={() => {
						void refetchAgent();
					}}
				/>
			)}
			{statsError && (
				<FleetReadError
					resource="agent statistics"
					cached={!!stats}
					pending={statsFetching}
					onRetry={() => {
						void refetchStats();
					}}
				/>
			)}
			{flaggedError && (
				<FleetReadError
					resource="flagged runs"
					cached={!!flaggedResp}
					pending={flaggedFetching}
					onRetry={() => {
						void refetchFlagged();
					}}
				/>
			)}
			{actionError && <TuningActionError message={actionError.message} />}
			{proposal ? (
				<TuningProposalEditor
					key={proposalKey}
					currentPrompt={currentPrompt}
					edits={edits}
					busy={busy}
					applying={applyTuning.isPending}
					actions={
						<>
							{generationAction}
							{evaluationAction}
						</>
					}
					onChange={(value) => {
						setEdits(value);
						setDryRun(null);
					}}
					onDiscard={handleDiscard}
					onApply={handleApply}
				/>
			) : (
				<div
					className="flex min-h-64 flex-col items-center justify-center gap-5 rounded-[var(--bf-radius-surface)] border border-dashed p-8 text-center lg:min-h-0 lg:flex-1"
					data-testid="editor-empty-state"
				>
					<Sparkles
						className="size-8 text-primary"
						aria-hidden="true"
					/>
					<div className="max-w-md space-y-2">
						<h2 className="text-lg font-semibold">
							Review a Proposed Prompt
						</h2>
						<p className="text-sm text-muted-foreground">
							Generate a proposal from flagged runs, review the
							changes, then edit or evaluate before applying.
						</p>
					</div>
					<div className="flex flex-wrap justify-center gap-2">
						{generationAction}
						{evaluationAction}
					</div>
				</div>
			)}
			<Sheet
				open={evidence !== null}
				onOpenChange={(open) => {
					if (!open) setEvidence(null);
				}}
			>
				<SheetContent
					className="sm:max-w-lg"
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						evidenceTrigger.current?.focus();
					}}
				>
					<SheetHeader>
						<SheetTitle>Tuning Evidence</SheetTitle>
						<SheetDescription>
							Supporting context for your prompt changes.
						</SheetDescription>
					</SheetHeader>
					<Tabs
						value={evidence ?? "runs"}
						onValueChange={(value) =>
							setEvidence(
								value as "runs" | "evaluation" | "context",
							)
						}
						className="min-h-0 gap-4 px-6 pb-6"
					>
						<TabsList className="h-auto! shrink-0 [&>button]:min-h-11">
							<TabsTrigger value="runs">Flagged Runs</TabsTrigger>
							<TabsTrigger value="evaluation">
								Evaluation
							</TabsTrigger>
							<TabsTrigger value="context">Context</TabsTrigger>
						</TabsList>
						<div className="max-h-[calc(100dvh-12rem)] overflow-auto">
							<TabsContent
								value="runs"
								className="m-0"
								data-testid="tune-pane-flagged"
							>
								<TuningFlaggedRuns
									runs={flagged}
									total={flaggedTotal}
									loading={flaggedLoading}
									error={flaggedError}
									cached={!!flaggedResp}
									fetching={flaggedFetching}
									onRetry={() => void refetchFlagged()}
									hasMore={hasNextPage}
									loadingMore={isFetchingNextPage}
									moreError={isFetchNextPageError}
									onLoadMore={() => void fetchNextPage()}
								/>
							</TabsContent>
							<TabsContent
								value="evaluation"
								className="m-0 space-y-4"
							>
								<p className="text-sm text-muted-foreground">
									A model assesses a past transcript using
									your proposed prompt. It does not run the
									agent’s tools again.
								</p>
								{dryRun ? (
									<TuningDryRunResults {...dryRun} />
								) : (
									<p>
										No evaluation for the current proposal.
										Run a dry-run to assess it.
									</p>
								)}
							</TabsContent>
							<TabsContent
								value="context"
								className="m-0 space-y-4"
							>
								{proposal?.summary && (
									<div className="space-y-2">
										<h3 className="font-semibold">
											Proposal Summary
										</h3>
										<p className="whitespace-pre-wrap text-sm [overflow-wrap:anywhere]">
											{proposal.summary}
										</p>
									</div>
								)}
								<h3 className="font-semibold">
									Current Prompt
								</h3>
								<pre className="whitespace-pre-wrap font-mono text-sm [overflow-wrap:anywhere]">
									{currentPrompt || "(no system prompt set)"}
								</pre>
							</TabsContent>
						</div>
					</Tabs>
				</SheetContent>
			</Sheet>
		</PageWorkspace>
	);
}
export default AgentTuneWorkbench;
