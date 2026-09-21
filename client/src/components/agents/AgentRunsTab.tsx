import { useMediaQuery } from "@/hooks/useMediaQuery";
/**
 * Runs tab for an agent's detail page.
 *
 * Lists this agent's runs with a search bar + verdict filter. Clicking a
 * RunCard opens the RunReviewSheet slide-over (for verdict + review chat).
 * Inline verdict toggles call `useSetVerdict` / `useClearVerdict` and
 * invalidate the run-list cache so subsequent fetches reflect the change.
 *
 * Composer state for the FlagConversation lives here; the parent page
 * is purely a router for tabs.
 */

import { useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, Sparkles, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
	CapturedDataFilter,
	conditionsToQueryParam,
	type MetadataFilterCondition,
} from "@/components/agents/CapturedDataFilter";
import { QueueBanner } from "@/components/agents/QueueBanner";
import { RunActionFeedback } from "./RunActionFeedback";
import { RunCard } from "@/components/agents/RunCard";
import { AgentRunSheet } from "./AgentRunSheet";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import {
	useClearVerdict,
	useInfiniteAgentRuns,
	useSetVerdict,
} from "@/services/agentRuns";
import { agentPlatform } from "@/services/agentPlatform";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];
type Verdict = "up" | "down" | null;
type ReviewSave = { runId: string; verdict: Verdict; note?: string };
type VerdictFilter = "all" | "up" | "down" | "unreviewed";

export interface AgentRunsTabProps {
	agentId: string;
}

export function AgentRunsTab({ agentId }: AgentRunsTabProps) {
	const shortDesktop = useMediaQuery(
		"(min-width: 1024px) and (max-height: 700px)",
	);
	const [searchParams, setSearchParams] = useSearchParams();
	const summaryFilter = searchParams.get("summary");
	const [query, setQuery] = useState("");
	const searchRef = useRef<HTMLInputElement>(null);
	const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("all");
	const [metadataConditions, setMetadataConditions] = useState<
		MetadataFilterCondition[]
	>([]);
	const [openRunId, setOpenRunId] = useState<string | null>(null);

	const queryClient = useQueryClient();
	const reviewBusy = useRef(false);
	const [pendingReview, setPendingReview] = useState<string | null>(null);
	const [failedReviews, setFailedReviews] = useState<
		Record<string, ReviewSave>
	>({});
	const [pendingRecordedRunId, setPendingRecordedRunId] = useState<
		string | null
	>(null);
	const [failedRecordedRunId, setFailedRecordedRunId] = useState<
		string | null
	>(null);
	const [recordedEvaluationLinks, setRecordedEvaluationLinks] = useState<
		Record<string, string>
	>({});

	const metadataFilter = conditionsToQueryParam(metadataConditions);

	const {
		data: runsPages,
		isLoading,
		isError,
		isFetchNextPageError,
		isFetching,
		refetch,
		hasNextPage,
		isFetchingNextPage,
		fetchNextPage,
	} = useInfiniteAgentRuns({
		agentId,
		q: query || undefined,
		verdict: verdictFilter !== "all" ? verdictFilter : undefined,
		metadataFilter,
	});

	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	useAgentRunUpdates({ agentId });

	const runs = useMemo(() => {
		const all = (runsPages?.pages.flatMap((p) => p.items) ??
			[]) as unknown as AgentRun[];
		if (summaryFilter === "failed") {
			return all.filter((r) => r.summary_status === "failed");
		}
		return all;
	}, [runsPages, summaryFilter]);
	const flaggedCount = useMemo(
		() => runs.filter((r) => r.verdict === "down").length,
		[runs],
	);

	function saveReview(change: ReviewSave) {
		if (reviewBusy.current) return;
		reviewBusy.current = true;
		setPendingReview(change.runId);
		setFailedReviews((previous) => {
			const next = { ...previous };
			delete next[change.runId];
			return next;
		});
		const callbacks = {
			onSuccess: () => {
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs"],
				});
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs-infinite"],
				});
				void queryClient.invalidateQueries({
					queryKey: ["get", "/api/agent-runs/{run_id}"],
				});
				if (change.note !== undefined)
					toast.success(change.note ? "Note saved" : "Note cleared");
			},
			onError: () =>
				setFailedReviews((previous) => ({
					...previous,
					[change.runId]: change,
				})),
			onSettled: () => {
				reviewBusy.current = false;
				setPendingReview(null);
			},
		};
		if (change.verdict === null) {
			clearVerdict.mutate(
				{ params: { path: { run_id: change.runId } } },
				callbacks,
			);
		} else {
			setVerdict.mutate(
				{
					params: { path: { run_id: change.runId } },
					body: {
						verdict: change.verdict,
						...(change.note !== undefined
							? { note: change.note || null }
							: {}),
					},
				},
				callbacks,
			);
		}
	}
	function applyVerdict(runId: string, verdict: Verdict) {
		saveReview({ runId, verdict });
	}
	function applyNote(runId: string, note: string) {
		saveReview({ runId, verdict: "down", note });
	}

	async function queueRecordedEvaluation(run: AgentRun) {
		if (pendingRecordedRunId || !run.agent_id) return;
		setPendingRecordedRunId(run.id);
		setFailedRecordedRunId(null);
		try {
			const accepted = await agentPlatform.recordedEvaluation({
				agent_id: run.agent_id,
				run_ids: [run.id],
				all_tests: true,
				applicability: "unknown",
				judge_mode: "exact",
				applicability_overrides: [],
			});
			setRecordedEvaluationLinks((previous) => ({
				...previous,
				[run.id]: `/agents/${run.agent_id}/quality?collection=tests&recorded=${accepted.evaluationId}`,
			}));
			toast.success("Recorded evaluation queued");
		} catch {
			setFailedRecordedRunId(run.id);
		} finally {
			setPendingRecordedRunId(null);
		}
	}

	return (
		<div
			className={`agent-runs-tab flex min-w-0 flex-col gap-4 ${shortDesktop ? "" : "lg:h-full lg:min-h-0"}`}
		>
			{/* Search + filter bar */}
			<div className="flex shrink-0 flex-wrap items-center gap-3">
				<div className="relative min-w-0 flex-[1_1_15rem] max-w-md">
					<Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
					<Input
						ref={searchRef}
						aria-label="Search runs"
						placeholder='Search — "ticket #123", "acme"…'
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						className="min-h-11 pl-8 pr-12"
					/>
					{query ? (
						<button
							type="button"
							aria-label="Clear search"
							onClick={() => {
								setQuery("");
								searchRef.current?.focus();
							}}
							className="absolute right-0 top-0 inline-flex size-11 items-center justify-center rounded-[var(--bf-radius-control)] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<X className="h-3.5 w-3.5" />
						</button>
					) : null}
				</div>

				<Select
					value={verdictFilter}
					onValueChange={(v) => setVerdictFilter(v as VerdictFilter)}
				>
					<SelectTrigger
						className="min-h-11 w-full sm:w-[160px]"
						aria-label="Verdict filter"
					>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						<SelectItem className="min-h-11" value="all">
							All verdicts
						</SelectItem>
						<SelectItem className="min-h-11" value="up">
							Good
						</SelectItem>
						<SelectItem className="min-h-11" value="down">
							Wrong
						</SelectItem>
						<SelectItem className="min-h-11" value="unreviewed">
							Unreviewed
						</SelectItem>
					</SelectContent>
				</Select>

				{summaryFilter === "failed" ? (
					<Badge variant="warning" className="gap-1">
						Summary failed
						<button
							type="button"
							aria-label="Clear summary filter"
							onClick={() => {
								const next = new URLSearchParams(searchParams);
								next.delete("summary");
								setSearchParams(next, { replace: true });
							}}
							className="ml-1 inline-flex size-11 items-center justify-center rounded-[var(--bf-radius-control)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<X className="h-3 w-3" />
						</button>
					</Badge>
				) : null}
				{flaggedCount > 0 ? (
					<Badge variant="destructive" className="ml-auto">
						{flaggedCount} flagged
					</Badge>
				) : null}
			</div>

			<div
				className={`agent-runs-filter-region shrink-0 ${shortDesktop ? "" : "lg:max-h-[40%] lg:overflow-auto"}`}
				role="region"
				aria-label="Run filter controls"
			>
				<CapturedDataFilter
					agentId={agentId}
					value={metadataConditions}
					onChange={setMetadataConditions}
				/>
			</div>

			{flaggedCount > 0 ? (
				<QueueBanner
					count={flaggedCount}
					actionLabel="Review runs"
					actionHref={`/agents/${agentId}/quality`}
				/>
			) : null}

			{isError && (
				<div
					role="alert"
					className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--bf-radius-surface)] border p-4 text-sm"
				>
					<p>
						{isFetchNextPageError
							? "Could not load more runs."
							: "Could not load runs."}
					</p>
					<Button
						type="button"
						variant="outline"
						className="min-h-11"
						aria-label="Retry runs"
						disabled={isFetching}
						onClick={() =>
							void (isFetchNextPageError
								? fetchNextPage()
								: refetch())
						}
					>
						{isFetching ? "Retrying…" : "Retry"}
					</Button>
				</div>
			)}
			{/* Run list */}
			<div
				className={`agent-runs-scroll-region flex flex-col gap-2 [&>*]:shrink-0 ${shortDesktop ? "" : "lg:min-h-0 lg:flex-1 lg:overflow-auto"}`}
				role="region"
				aria-label="Run history"
			>
				{isLoading ? (
					<>
						<Skeleton className="h-20 w-full" />
						<Skeleton className="h-20 w-full" />
						<Skeleton className="h-20 w-full" />
					</>
				) : isError && !runsPages ? null : runs.length === 0 ? (
					<p className="rounded-[var(--bf-radius-surface)] border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
						No runs match this filter.
					</p>
				) : (
					<>
						{runs.map((r) => (
							<div
								key={r.id}
								className="rounded-[var(--bf-radius-surface)] border bg-card"
							>
								<RunCard
									run={r}
									verdict={(r.verdict as Verdict) ?? null}
									highlight={query}
									onOpen={() => setOpenRunId(r.id)}
									onVerdict={(v) => applyVerdict(r.id, v)}
									onNote={applyNote}
									reviewDisabled={pendingReview !== null}
									reviewFeedback={
										pendingReview === r.id ||
										Boolean(failedReviews[r.id]) ? (
											<RunActionFeedback
												pending={pendingReview === r.id}
												failed={Boolean(
													failedReviews[r.id],
												)}
												onRetry={() => {
													if (failedReviews[r.id])
														saveReview(
															failedReviews[r.id],
														);
												}}
											/>
										) : null
									}
								/>
								<div className="flex flex-wrap items-center gap-2 border-t px-4 py-3">
									<Button
										type="button"
										variant="outline"
										size="sm"
										className="min-h-11"
										disabled={pendingRecordedRunId !== null}
										onClick={() =>
											void queueRecordedEvaluation(r)
										}
									>
										<Sparkles
											aria-hidden="true"
											className="size-4"
										/>
										Evaluate recorded run
									</Button>
									{recordedEvaluationLinks[r.id] ? (
										<Button
											asChild
											variant="ghost"
											size="sm"
											className="min-h-11"
										>
											<Link
												to={
													recordedEvaluationLinks[
														r.id
													]
												}
											>
												Open recorded evaluation
											</Link>
										</Button>
									) : null}
									<RunActionFeedback
										pending={pendingRecordedRunId === r.id}
										failed={failedRecordedRunId === r.id}
										onRetry={() =>
											void queueRecordedEvaluation(r)
										}
										message="Could not queue recorded evaluation. Try again when you are ready."
										pendingLabel="Queuing recorded evaluation…"
										retryLabel="Retry recorded evaluation"
									/>
								</div>
							</div>
						))}
						{isFetchingNextPage ? (
							<Skeleton className="h-20 w-full" />
						) : null}
						<InfiniteScrollSentinel
							hasNext={!!hasNextPage && !isError}
							isLoading={isFetchingNextPage}
							onLoadMore={() => fetchNextPage()}
						/>
					</>
				)}
			</div>

			<AgentRunSheet
				openRunId={openRunId}
				onClose={() => setOpenRunId(null)}
			/>
		</div>
	);
}
