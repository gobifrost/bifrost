/**
 * AgentReviewPage — focused review queue for an agent's flagged runs.
 *
 * Routes:
 *   /agents/:id/review  → this page
 *
 * Layout:
 *   - Header: agent name, "Review N of total" counter, dot pagination,
 *     keyboard shortcut hints, link to consolidated tuning when there is
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

import { useCallback, useEffect, useMemo, useState } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useParams,
} from "react-router-dom";
import {
	ArrowLeft,
	CheckCircle,
	ChevronLeft,
	ChevronRight,
	Keyboard,
	Sparkles,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import {
	RunReviewPanel,
	type Verdict,
} from "@/components/agents/RunReviewPanel";
import { useAgent } from "@/hooks/useAgents";
import {
	useAgentRun,
	useAgentRuns,
	useClearVerdict,
	useSetVerdict,
} from "@/services/agentRuns";
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

	const { data: agent } = useAgent(agentId);
	const runNavigationOrigin: AgentRunNavigationOrigin = {
		href: getLocationHref(location),
		label: `Back to ${agent?.name ?? "agent"} review queue`,
	};

	// Queue: flagged runs for this agent. Backend filter returns only the
	// completed flagged set we want to walk through.
	const { data: queueResp, isLoading } = useAgentRuns({
		agentId,
		verdict: "down",
	});

	const queue = useMemo<AgentRun[]>(
		() => (queueResp?.items ?? []) as AgentRun[],
		[queueResp],
	);

	const [idx, setIdx] = useState(0);

	// Keep idx in bounds when the queue shrinks (e.g. after applying a verdict).
	// Adjust during render rather than via setState-in-effect.
	if (queue.length === 0 && idx !== 0) {
		setIdx(0);
	} else if (queue.length > 0 && idx >= queue.length) {
		setIdx(queue.length - 1);
	}

	const current = queue[idx];
	const { data: rawDetail } = useAgentRun(current?.id);
	const detail = rawDetail as unknown as AgentRunDetailResponse | undefined;

	const setVerdict = useSetVerdict();
	const clearVerdict = useClearVerdict();
	const [note, setNote] = useState("");

	// Reset the note whenever we move to a new run. Adjust during render
	// rather than via a setState-in-effect cycle.
	const detailKey = `${detail?.id ?? ""}:${detail?.verdict_note ?? ""}`;
	const [prevDetailKey, setPrevDetailKey] = useState(detailKey);
	if (prevDetailKey !== detailKey) {
		setPrevDetailKey(detailKey);
		setNote((detail?.verdict_note as string | undefined) ?? "");
	}

	function invalidate() {
		queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
	}

	const advance = useCallback(() => {
		setIdx((i) => Math.min(queue.length - 1, i + 1));
	}, [queue.length]);

	const back = useCallback(() => {
		setIdx((i) => Math.max(0, i - 1));
	}, []);

	const handleVerdict = useCallback(
		(next: Verdict) => {
			if (!current) return;
			const onSuccess = () => {
				invalidate();
				// On any successful verdict change, advance to the next run.
				advance();
			};
			if (next === null) {
				clearVerdict.mutate(
					{ params: { path: { run_id: current.id } } },
					{ onSuccess },
				);
			} else {
				setVerdict.mutate(
					{
						params: { path: { run_id: current.id } },
						body: { verdict: next },
					},
					{ onSuccess },
				);
			}
		},
		// eslint-disable-next-line react-hooks/exhaustive-deps
		[current, advance],
	);

	// Keyboard shortcuts
	useEffect(() => {
		function onKey(e: KeyboardEvent) {
			const tag = (e.target as HTMLElement | null)?.tagName;
			if (tag === "INPUT" || tag === "TEXTAREA") return;
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

	if (isLoading) {
		return (
			<div className="flex flex-col gap-5 max-w-5xl mx-auto">
				<Skeleton className="h-6 w-32" />
				<Skeleton className="h-12 w-1/2" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (queue.length === 0) {
		return (
			<div
				className="flex flex-col gap-5 max-w-3xl mx-auto"
				data-testid="review-empty"
			>
				<Breadcrumb agentId={agentId} agentName={agent?.name} />
				<Card>
					<CardContent className="flex flex-col items-center justify-center gap-2 py-12 text-center">
						<CheckCircle className="h-10 w-10 text-emerald-500" />
						<div className="text-base font-medium">
							Nothing to review
						</div>
						<p className="text-sm text-muted-foreground">
							No flagged runs for this agent.
						</p>
					</CardContent>
				</Card>
			</div>
		);
	}

	const flaggedRemaining = queue.length;

	return (
		<div
			className="flex flex-col gap-4 max-w-5xl mx-auto"
			data-testid="review-flipbook"
		>
			<Breadcrumb agentId={agentId} agentName={agent?.name} />

			{/* Header */}
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="flex items-center gap-2 text-2xl font-extrabold tracking-tight">
						<Sparkles className="h-5 w-5" />
						Review runs
					</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						<span data-testid="review-counter">
							{idx + 1} of {queue.length}
						</span>{" "}
						·{" "}
						<span className="text-rose-600 dark:text-rose-400">
							{flaggedRemaining} flagged
						</span>
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-3">
					<span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
						<Keyboard className="h-3.5 w-3.5" />
						<Kbd>←/→</Kbd> navigate
						<Kbd>U</Kbd>/<Kbd>D</Kbd> verdict
						<Kbd>Esc</Kbd> exit
					</span>
					{flaggedRemaining > 0 ? (
						<Button asChild>
							<Link to={`/agents/${agentId}/tune`}>
								<Sparkles className="h-4 w-4" />
								Tune with {flaggedRemaining} flagged
							</Link>
						</Button>
					) : null}
				</div>
			</div>

			{/* Flipbook card */}
			{detail ? (
				<FlipbookCard
					key={detail.id}
					run={detail}
					verdict={
						((detail.verdict as Verdict | undefined) ?? null) as Verdict
					}
					note={note}
					onVerdict={handleVerdict}
					onNote={setNote}
					runNavigationOrigin={runNavigationOrigin}
				/>
			) : (
				<Skeleton className="h-96 w-full" />
			)}

			{/* Footer nav */}
			<div className="flex items-center justify-between gap-3">
				<Button
					variant="outline"
					onClick={back}
					disabled={idx === 0}
					data-testid="prev-button"
				>
					<ChevronLeft className="h-4 w-4" />
					Previous
				</Button>
				<ProgressDots
					queue={queue}
					idx={idx}
					onJump={(i) => setIdx(i)}
				/>
				<Button
					variant="outline"
					onClick={advance}
					disabled={idx === queue.length - 1}
					data-testid="next-button"
				>
					Next
					<ChevronRight className="h-4 w-4" />
				</Button>
			</div>
		</div>
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
			className="inline-flex w-fit items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
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
							{run.did || run.asked || "Agent run"}
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
		<div className="flex items-center gap-1" data-testid="progress-dots">
			{queue.map((r, i) => (
				<button
					key={r.id}
					type="button"
					aria-label={`Go to run ${i + 1}`}
					title={r.did ?? r.asked ?? `Run ${i + 1}`}
					onClick={() => onJump(i)}
					className={cn(
						"h-1.5 rounded-full transition-all",
						i === idx
							? "w-5 bg-foreground"
							: "w-2 bg-border hover:bg-muted-foreground",
					)}
				/>
			))}
		</div>
	);
}

export default AgentReviewPage;
