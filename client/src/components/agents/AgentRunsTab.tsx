/**
 * Runs tab for an agent's detail page.
 *
 * Lists this agent's runs with a search bar + verdict filter. Clicking a
 * RunCard opens the RunReviewSheet slide-over (for verdict + tuning chat).
 * Inline verdict toggles call `useSetVerdict` / `useClearVerdict` and
 * invalidate the run-list cache so subsequent fetches reflect the change.
 *
 * Composer state for the FlagConversation lives here; the parent page
 * is purely a router for tabs.
 */

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

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
import { RunCard } from "@/components/agents/RunCard";
import { RunReviewSheet } from "@/components/agents/RunReviewSheet";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { useAgentRunUpdates } from "@/hooks/useAgentRunUpdates";
import {
	useAgentRun,
	useClearVerdict,
	useFlagConversation,
	useInfiniteAgentRuns,
	useSendFlagMessage,
	useSetVerdict,
} from "@/services/agentRuns";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];
type Verdict = "up" | "down" | null;
type VerdictFilter = "all" | "up" | "down" | "unreviewed";

export interface AgentRunsTabProps {
	agentId: string;
}

export function AgentRunsTab({ agentId }: AgentRunsTabProps) {
	const [searchParams, setSearchParams] = useSearchParams();
	const summaryFilter = searchParams.get("summary");
	const [query, setQuery] = useState("");
	const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("all");
	const [metadataConditions, setMetadataConditions] = useState<
		MetadataFilterCondition[]
	>([]);
	const [openRunId, setOpenRunId] = useState<string | null>(null);

	const queryClient = useQueryClient();

	const metadataFilter = conditionsToQueryParam(metadataConditions);

	const {
		data: runsPages,
		isLoading,
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

	function applyVerdict(runId: string, next: Verdict) {
		const onSuccess = () => {
			queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
			queryClient.invalidateQueries({ queryKey: ["agent-runs-infinite"] });
		};
		if (next === null) {
			clearVerdict.mutate(
				{ params: { path: { run_id: runId } } },
				{ onSuccess },
			);
		} else {
			setVerdict.mutate(
				{
					params: { path: { run_id: runId } },
					body: { verdict: next },
				},
				{ onSuccess },
			);
		}
	}

	function applyNote(runId: string, note: string) {
		// Re-submits the "down" verdict with the new note populated. The backend
		// accepts verdict + note together; clearing the note is just an empty
		// string. Assumes verdict is already "down" — the caller (RunCard) only
		// exposes the note input in that state.
		setVerdict.mutate(
			{
				params: { path: { run_id: runId } },
				body: { verdict: "down", note: note || null },
			},
			{
				onSuccess: () => {
					queryClient.invalidateQueries({ queryKey: ["agent-runs"] });
			queryClient.invalidateQueries({ queryKey: ["agent-runs-infinite"] });
					toast.success(note ? "Note saved" : "Note cleared");
				},
				onError: () => {
					toast.error("Failed to save note");
				},
			},
		);
	}

	return (
		<div className="agent-runs-tab flex flex-col gap-4">
			{/* Search + filter bar */}
			<div className="flex flex-wrap items-center gap-3">
				<div className="relative flex-1 min-w-[240px] max-w-md">
					<Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
					<Input
						aria-label="Search runs"
						placeholder='Search — "ticket #123", "acme"…'
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						className="pl-8 pr-8"
					/>
					{query ? (
						<button
							type="button"
							aria-label="Clear search"
							onClick={() => setQuery("")}
							className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
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
						className="w-[160px]"
						aria-label="Verdict filter"
					>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						<SelectItem value="all">All verdicts</SelectItem>
						<SelectItem value="up">Good</SelectItem>
						<SelectItem value="down">Wrong</SelectItem>
						<SelectItem value="unreviewed">Unreviewed</SelectItem>
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
							className="ml-0.5 inline-flex items-center"
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

			<div className="agent-runs-filter-region">
				<CapturedDataFilter
					agentId={agentId}
					value={metadataConditions}
					onChange={setMetadataConditions}
				/>
			</div>

			{flaggedCount > 0 ? (
				<QueueBanner
					count={flaggedCount}
					actionLabel="Open tuning"
					actionHref={`/agents/${agentId}/tune`}
				/>
			) : null}

			{/* Run list */}
			<div
				className="agent-runs-scroll-region flex flex-col gap-2"
				role="region"
				aria-label="Run history"
			>
				{isLoading ? (
					<>
						<Skeleton className="h-20 w-full" />
						<Skeleton className="h-20 w-full" />
						<Skeleton className="h-20 w-full" />
					</>
				) : runs.length === 0 ? (
					<p className="rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10 py-8 text-center text-sm text-muted-foreground">
						No runs match this filter.
					</p>
				) : (
					<>
						{runs.map((r) => (
							<RunCard
								key={r.id}
								run={r}
								verdict={(r.verdict as Verdict) ?? null}
								highlight={query}
								onOpen={() => setOpenRunId(r.id)}
								onVerdict={(v) => applyVerdict(r.id, v)}
								onNote={applyNote}
							/>
						))}
						{isFetchingNextPage ? (
							<Skeleton className="h-20 w-full" />
						) : null}
						<InfiniteScrollSentinel
							hasNext={!!hasNextPage}
							isLoading={isFetchingNextPage}
							onLoadMore={() => fetchNextPage()}
						/>
					</>
				)}
			</div>

			<RunSheet
				agentId={agentId}
				openRunId={openRunId}
				onClose={() => setOpenRunId(null)}
				onVerdictChange={(v) =>
					openRunId ? applyVerdict(openRunId, v) : null
				}
			/>
		</div>
	);
}

interface RunSheetProps {
	agentId: string;
	openRunId: string | null;
	onClose: () => void;
	onVerdictChange: (v: Verdict) => void;
}

function RunSheet({
	agentId,
	openRunId,
	onClose,
	onVerdictChange,
}: RunSheetProps) {
	void agentId;
	const { data: runDetail } = useAgentRun(openRunId ?? undefined);
	const { data: conversation } = useFlagConversation(
		openRunId ?? undefined,
	);
	const sendMessage = useSendFlagMessage();
	const [note, setNote] = useState("");

	function onSendChat(text: string) {
		if (!openRunId) return;
		sendMessage.mutate({
			params: { path: { run_id: openRunId } },
			body: { content: text },
		});
	}

	const open = !!openRunId;
	const verdict =
		(((runDetail as unknown as { verdict?: Verdict })?.verdict ??
			null) as Verdict) ?? null;

	return (
		<RunReviewSheet
			open={open}
			onOpenChange={(o) => (o ? null : onClose())}
			run={runDetail as never}
			verdict={verdict}
			note={note}
			onVerdict={onVerdictChange}
			onNote={setNote}
			conversation={conversation ?? null}
			onSendChat={onSendChat}
			chatPending={sendMessage.isPending}
			defaultTab={verdict === "down" ? "tune" : "review"}
		/>
	);
}
