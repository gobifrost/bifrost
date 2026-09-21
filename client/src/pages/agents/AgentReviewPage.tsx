import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
/**
 * AgentReviewPage — focused review queue for an agent's flagged runs.
 *
 * Routes:
 *   /agents/:id/review  → this page
 *
 * Layout:
 *   - Header: agent name, "Review N of total" counter, dot pagination,
 *     keyboard shortcut hints, link to the quality page when there is
 *     anything still flagged.
 *   - Main: a Card with the run summary header + <RunReviewPanel
 *     variant="flipbook"> with verdict actions.
 *   - Bottom: Previous / Next buttons.
 *   - Keyboard: ←/→ navigate, U/D set verdict, Esc returns to agent.
 *   - Verdict actions auto-advance to the next run.
 *
 * Ported from /tmp/agent-mockup/src/pages/ReviewFlipbookPage.tsx — shadcn
 * primitives, Tailwind, no inline styles, real hooks.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
	ArrowLeft,
	CheckCircle,
	ChevronLeft,
	ChevronRight,
	Keyboard,
	Sparkles,
} from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { Skeleton } from "@/components/ui/skeleton";

import {
	RunReviewPanel,
	type Verdict,
} from "@/components/agents/RunReviewPanel";
import { FleetReadError } from "./FleetReadError";
import { useAgent } from "@/hooks/useAgents";
import {
	useAgentRun,
	useInfiniteAgentRuns,
	useClearVerdict,
	useSetVerdict,
} from "@/services/agentRuns";
import { agentPlatform } from "@/services/agentPlatform";
import {
	cn,
	formatCost,
	formatDuration,
	formatNumber,
	formatRelativeTime,
} from "@/lib/utils";
import {
	createAgentRunNavigationState,
	getLocationHref,
	type AgentRunNavigationOrigin,
} from "@/lib/agent-run-navigation";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];
type AgentRunDetailResponse = components["schemas"]["AgentRunDetailResponse"];

export function AgentReviewPage() {
	const { id: agentId } = useParams<{ id: string }>();
	const navigate = useNavigate();
	const location = useLocation();
	const queryClient = useQueryClient();

	const {
		data: agent,
		isError: agentError,
		isFetching: agentFetching,
		refetch: refetchAgent,
	} = useAgent(agentId);
	const runNavigationOrigin: AgentRunNavigationOrigin = {
		href: getLocationHref(location),
		label: `Back to ${agent?.name ?? "agent"} review queue`,
	};

	// Queue: flagged runs for this agent. Backend filter returns only the
	// completed flagged set we want to walk through.
	const {
		data: queueResp,
		isLoading,
		isError: queueReadError,
		isFetching: queueFetching,
		refetch: refetchQueue,
		hasNextPage,
		fetchNextPage,
		isFetchingNextPage,
		isFetchNextPageError,
	} = useInfiniteAgentRuns({
		agentId,
		verdict: "down",
	});

	const queue = useMemo<AgentRun[]>(
		() =>
			(queueResp?.pages.flatMap((page) => page.items) ??
				[]) as AgentRun[],
		[queueResp],
	);

	const queueError = queueReadError && !isFetchNextPageError;
	const queueTotal = queueResp?.pages[0]?.total ?? queue.length;
	const [selectedRunId, setSelectedRunId] = useState<string>();
	const idx = Math.max(
		0,
		queue.findIndex((run) => run.id === selectedRunId),
	);
	const savingRef = useRef(false);
	const [saving, setSaving] = useState(false);
	const saveErrorRef = useRef<HTMLDivElement>(null);
	const [saveError, setSaveError] = useState<{
		runId: string;
		verdict: Verdict;
	} | null>(null);

	useEffect(() => {
		if (saveError) {
			saveErrorRef.current?.focus();
			saveErrorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [saveError]);

	const current = queue[idx];
	const {
		data: rawDetail,
		isError: detailError,
		isFetching: detailFetching,
		refetch: refetchDetail,
	} = useAgentRun(current?.id);
	const detail = rawDetail as unknown as AgentRunDetailResponse | undefined;

	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	const recordFinding = useMutation({
		mutationFn: (input: { runId: string; note: string }) => {
			if (!agentId) throw new Error("Run unavailable");
			return agentPlatform.createFinding({
				agent_id: agentId,
				description:
					input.note.trim() ||
					`Observed problem in run ${input.runId.slice(0, 8)}.`,
				expected_behavior: null,
				source_kind: "run",
				finding_kind: "problem",
				source_run_id: input.runId,
				source_sequence: null,
				external_ref: null,
			});
		},
		onSuccess: () => {
			toast.success("Finding recorded", {
				description: "Review it on the Quality page under Findings.",
				action: agentId
					? {
							label: "Open Findings",
							onClick: () =>
								navigate(
									`/agents/${agentId}/quality?collection=findings`,
								),
						}
					: undefined,
			});
		},
		onError: () => toast.error("Could not record the finding."),
	});
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const note = current
		? (drafts[current.id] ?? detail?.verdict_note ?? "")
		: "";
	const setNote = (value: string) => {
		if (current)
			setDrafts((previous) => ({ ...previous, [current.id]: value }));
	};

	const advance = useCallback(() => {
		if (!savingRef.current)
			setSelectedRunId(queue[Math.min(queue.length - 1, idx + 1)]?.id);
	}, [queue, idx]);

	const back = useCallback(() => {
		if (!savingRef.current)
			setSelectedRunId(queue[Math.max(0, idx - 1)]?.id);
	}, [queue, idx]);

	const handleVerdict = useCallback(
		(next: Verdict) => {
			if (!current || !detail || savingRef.current) return;
			savingRef.current = true;
			setSaving(true);
			setSaveError(null);
			const nextRunId =
				queue[idx + 1]?.id ??
				(next === "down"
					? current.id
					: (queue[idx - 1]?.id ?? current.id));
			const onSuccess = () => {
				if (next === null)
					setDrafts((previous) => {
						const updated = { ...previous };
						delete updated[current.id];
						return updated;
					});
				setSelectedRunId(nextRunId);
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs"],
				});
				void queryClient.invalidateQueries({
					queryKey: ["agent-runs-infinite"],
				});
			};
			const onError = () =>
				setSaveError({ runId: current.id, verdict: next });
			const onSettled = () => {
				savingRef.current = false;
				setSaving(false);
			};
			if (next === null) {
				clearVerdict.mutate(
					{ params: { path: { run_id: current.id } } },
					{ onSuccess, onError, onSettled },
				);
			} else {
				setVerdict.mutate(
					{
						params: { path: { run_id: current.id } },
						body: { verdict: next, note },
					},
					{ onSuccess, onError, onSettled },
				);
			}
		},
		[
			current,
			detail,
			queue,
			idx,
			queryClient,
			clearVerdict,
			setVerdict,
			note,
		],
	);

	// Keyboard shortcuts
	useEffect(() => {
		function onKey(e: KeyboardEvent) {
			if (
				savingRef.current ||
				e.defaultPrevented ||
				e.ctrlKey ||
				e.metaKey ||
				e.altKey ||
				e.isComposing
			)
				return;
			const target = e.target instanceof HTMLElement ? e.target : null;
			if (
				target?.closest(
					'input, textarea, select, button, a, [contenteditable="true"], [role="dialog"], [role="menu"], [role="listbox"], [role="combobox"]',
				)
			)
				return;
			if (e.key === "ArrowRight" || e.key === "j") {
				e.preventDefault();
				advance();
			} else if (e.key === "ArrowLeft" || e.key === "k") {
				e.preventDefault();
				back();
			} else if (e.key === "u" || e.key === "U") {
				e.preventDefault();
				handleVerdict("up");
			} else if (e.key === "d" || e.key === "D") {
				e.preventDefault();
				handleVerdict("down");
			} else if (e.key === "Escape") {
				e.preventDefault();
				if (agentId) navigate(`/agents/${agentId}`);
			}
		}
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [advance, back, handleVerdict, navigate, agentId]);

	const agentHeader = (
		<>
			<Breadcrumb agentId={agentId} agentName={agent?.name} />
			{agentError ? (
				<FleetReadError
					resource="agent information"
					cached={!!agent}
					pending={agentFetching}
					onRetry={() => void refetchAgent()}
				/>
			) : null}
		</>
	);

	if (isLoading) {
		return (
			<div className="flex flex-col gap-5 max-w-5xl mx-auto">
				<Skeleton className="h-6 w-32" />
				<Skeleton className="h-12 w-1/2" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (queueError && !queueResp) {
		return (
			<div className="mx-auto flex max-w-5xl flex-col gap-4">
				{agentHeader}
				<h1 className="font-display text-2xl font-semibold">
					Review runs
				</h1>
				<FleetReadError
					resource="review queue"
					cached={false}
					pending={queueFetching}
					onRetry={() => void refetchQueue()}
				/>
			</div>
		);
	}

	if (queue.length === 0) {
		return (
			<div
				className="flex flex-col gap-5 max-w-3xl mx-auto"
				data-testid="review-empty"
			>
				{agentHeader}
				{queueError ? (
					<FleetReadError
						resource="review queue"
						cached
						pending={queueFetching}
						onRetry={() => void refetchQueue()}
					/>
				) : null}
				<Card>
					<CardContent className="flex flex-col items-center justify-center gap-2 py-12 text-center">
						<CheckCircle className="h-10 w-10 text-[var(--bf-success)]" />
						<h1 className="font-display text-xl font-semibold">
							Nothing to review
						</h1>
						<p className="text-sm text-muted-foreground">
							No flagged runs for this agent.
						</p>
					</CardContent>
				</Card>
			</div>
		);
	}

	const flaggedRemaining = queueTotal;

	return (
		<PageWorkspace
			className="flex flex-col gap-4 max-w-5xl mx-auto"
			data-testid="review-flipbook"
		>
			<div className="shrink-0 space-y-6">
				{agentHeader}

				{/* Header */}
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<h1 className="flex items-center gap-2 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
							<Sparkles className="h-5 w-5" />
							Review runs
						</h1>
						<p className="mt-1 text-sm text-muted-foreground">
							<span data-testid="review-counter">
								{idx + 1} of {queueTotal}
							</span>{" "}
							·{" "}
							<span className="text-[var(--bf-danger)]">
								{flaggedRemaining} flagged
							</span>
						</p>
					</div>
					<div className="flex flex-wrap items-center gap-3">
						<span className="hidden items-center gap-2 text-xs text-muted-foreground sm:inline-flex">
							<Keyboard className="h-3.5 w-3.5" />
							<Kbd>←/→</Kbd> navigate
							<Kbd>U</Kbd>/<Kbd>D</Kbd> verdict
							<Kbd>Esc</Kbd> exit
						</span>
						{flaggedRemaining > 0 ? (
							<Button asChild>
								<Link
									to={`/agents/${agentId}/quality?collection=findings`}
								>
									<Sparkles className="h-4 w-4" />
									Open Quality
								</Link>
							</Button>
						) : null}
					</div>
				</div>
			</div>

			<PageScrollArea className="space-y-6">
				{queueError ? (
					<FleetReadError
						resource="review queue"
						cached
						pending={queueFetching}
						onRetry={() => void refetchQueue()}
					/>
				) : null}
				{detailError ? (
					<FleetReadError
						resource="run details"
						cached={!!detail}
						pending={detailFetching}
						onRetry={() => void refetchDetail()}
					/>
				) : null}

				{saving ? (
					<p role="status" className="text-sm text-muted-foreground">
						Saving review…
					</p>
				) : null}
				{saveError?.runId === current?.id ? (
					<div
						role="alert"
						ref={saveErrorRef}
						tabIndex={-1}
						className="space-y-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
					>
						<p>
							Could not save your review. Your note is still here.
						</p>
						<Button
							variant="outline"
							onClick={() => handleVerdict(saveError.verdict)}
						>
							Retry review
						</Button>
					</div>
				) : null}
				{/* Flipbook card */}
				{detail ? (
					<fieldset
						disabled={saving}
						className="min-w-0"
						aria-label="Run review"
					>
						<FlipbookCard
							key={detail.id}
							run={detail}
							verdict={
								((detail.verdict as Verdict | undefined) ??
									null) as Verdict
							}
							note={note}
							onVerdict={handleVerdict}
							onNote={setNote}
							runNavigationOrigin={runNavigationOrigin}
						/>
						{note !== (detail.verdict_note ?? "") &&
						(detail.verdict === "up" ||
							detail.verdict === "down") ? (
							<Button
								className="mt-3"
								onClick={() =>
									handleVerdict(detail.verdict as Verdict)
								}
							>
								Save note and continue
							</Button>
						) : null}
						<Button
							variant="outline"
							className="mt-3 ml-2"
							disabled={recordFinding.isPending || !current}
							onClick={() =>
								current &&
								recordFinding.mutate({
									runId: current.id,
									note,
								})
							}
						>
							{recordFinding.isPending
								? "Recording…"
								: "Record Finding from This Run"}
						</Button>
					</fieldset>
				) : !detailError ? (
					<Skeleton className="h-96 w-full" />
				) : null}
			</PageScrollArea>

			{/* Footer nav */}
			<div className="flex flex-wrap items-center justify-between gap-3">
				<Button
					variant="outline"
					onClick={back}
					disabled={saving || idx === 0}
					className="min-h-11"
					data-testid="prev-button"
				>
					<ChevronLeft className="h-4 w-4" />
					Previous
				</Button>
				<ProgressDots
					queue={queue}
					idx={idx}
					onJump={(i) => {
						if (!savingRef.current) setSelectedRunId(queue[i]?.id);
					}}
				/>
				<Button
					variant="outline"
					onClick={advance}
					disabled={saving || idx === queue.length - 1}
					className="min-h-11"
					data-testid="next-button"
				>
					Next
					<ChevronRight className="h-4 w-4" />
				</Button>
			</div>
			{hasNextPage ? (
				<div className="space-y-2">
					{isFetchNextPageError ? (
						<p
							role="alert"
							className="text-sm text-[var(--bf-danger)]"
						>
							Could not load more runs. Your loaded reviews are
							still available.
						</p>
					) : null}
					<Button
						variant="outline"
						disabled={saving || queueFetching}
						onClick={() => void fetchNextPage()}
					>
						{isFetchingNextPage
							? "Loading more runs…"
							: isFetchNextPageError
								? "Retry loading more runs"
								: "Load more flagged runs"}
					</Button>
					<p className="text-xs text-muted-foreground">
						{queue.length} of {queueTotal} runs loaded
					</p>
				</div>
			) : null}
		</PageWorkspace>
	);
}

function Breadcrumb({
	agentId,
	agentName,
}: {
	agentId: string | undefined;
	agentName: string | undefined;
}) {
	return (
		<Link
			to={agentId ? `/agents/${agentId}` : "/agents"}
			className="inline-flex min-h-11 w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
		>
			<ArrowLeft className="h-3 w-3" />
			{agentName ?? "Back to agent"}
		</Link>
	);
}

function Kbd({ children }: { children: React.ReactNode }) {
	return (
		<kbd className="rounded border border-b-2 bg-muted px-1.5 py-0.5 font-mono text-[10.5px] text-foreground">
			{children}
		</kbd>
	);
}

function FlipbookCard({
	run,
	verdict,
	note,
	onVerdict,
	onNote,
	runNavigationOrigin,
}: {
	run: AgentRunDetailResponse;
	verdict: Verdict;
	note: string;
	onVerdict: (v: Verdict) => void;
	onNote: (n: string) => void;
	runNavigationOrigin: AgentRunNavigationOrigin;
}) {
	const startedAt = run.started_at ?? run.created_at;
	return (
		<Card className="overflow-hidden">
			<CardHeader className="pb-3">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<div className="text-xs text-muted-foreground">
							{formatRelativeTime(startedAt)} ·{" "}
							{run.duration_ms != null
								? formatDuration(run.duration_ms)
								: "—"}{" "}
							· {run.iterations_used} iter ·{" "}
							{formatNumber(run.tokens_used)} tok
							{run.ai_totals?.total_cost
								? ` · ${formatCost(run.ai_totals.total_cost)}`
								: ""}
						</div>
						<CardTitle className="mt-1 text-base">
							{run.did || run.asked ? (
								<MarkdownContent
									content={run.did || run.asked || ""}
									variant="preview"
								/>
							) : (
								"Agent run"
							)}
						</CardTitle>
					</div>
					<Button asChild variant="ghost" size="sm">
						<Link
							to={`/agents/${run.agent_id}/runs/${run.id}`}
							state={createAgentRunNavigationState(
								runNavigationOrigin,
							)}
							data-testid="open-detail"
						>
							Open full detail
							<ChevronRight className="h-4 w-4" />
						</Link>
					</Button>
				</div>
			</CardHeader>
			<CardContent className="p-0">
				<RunReviewPanel
					run={run}
					variant="flipbook"
					verdict={verdict}
					note={note}
					onVerdict={onVerdict}
					onNote={onNote}
					runNavigationOrigin={runNavigationOrigin}
				/>
			</CardContent>
		</Card>
	);
}

function ProgressDots({
	queue,
	idx,
	onJump,
}: {
	queue: AgentRun[];
	idx: number;
	onJump: (i: number) => void;
}) {
	return (
		<div
			className="order-last flex w-full items-center overflow-x-auto sm:order-none sm:w-auto sm:max-w-[50%]"
			data-testid="progress-dots"
		>
			{queue.map((r, i) => (
				<button
					key={r.id}
					type="button"
					aria-current={i === idx ? "step" : undefined}
					aria-label={`Go to run ${i + 1}`}
					onClick={() => onJump(i)}
					className={cn(
						"flex size-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
						i === idx
							? "text-primary"
							: "text-muted-foreground hover:text-foreground",
					)}
				>
					<span
						aria-hidden="true"
						className={cn(
							"h-1.5 rounded-full bg-current",
							i === idx ? "w-5" : "w-2",
						)}
					/>
				</button>
			))}
		</div>
	);
}

export default AgentReviewPage;
