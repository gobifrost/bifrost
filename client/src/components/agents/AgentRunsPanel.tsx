/**
 * AgentRunsPanel — cross-agent runs table rendered inside ExecutionHistory
 * when the page is switched to the agents tab (`/history?type=agents`).
 *
 * Deliberately minimal vs the old AgentRunsTable: no org filter, no verdict
 * filter, no search. Users filter by clicking through to an agent. The
 * panel exists to answer "show me every recent agent run across the
 * fleet" — fleet-wide visibility, not a replacement for the per-agent
 * runs tab.
 */

import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
	AlertCircle,
	Bot,
	CheckCircle,
	Clock,
	Loader2,
	RefreshCw,
	ThumbsDown,
	ThumbsUp,
	XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";

import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import {
	useAgentRunListStream,
	useInfiniteAgentRuns,
	useRerunAgentRun,
} from "@/services/agentRuns";
import {
	createAgentRunNavigationState,
	getLocationHref,
} from "@/lib/agent-run-navigation";
import { formatDate, formatDuration } from "@/lib/utils";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];

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

function VerdictGlyph({ verdict }: { verdict: AgentRun["verdict"] }) {
	if (verdict === "up") {
		return <ThumbsUp className="h-3 w-3 text-emerald-500" aria-label="Approved" />;
	}
	if (verdict === "down") {
		return <ThumbsDown className="h-3 w-3 text-rose-500" aria-label="Flagged" />;
	}
	return null;
}

export function AgentRunsPanel() {
	const navigate = useNavigate();
	const location = useLocation();
	const runNavigationState = createAgentRunNavigationState({
		href: getLocationHref(location),
		label: "Back to run history",
	});
	const {
		data,
		isLoading,
		hasNextPage,
		isFetchingNextPage,
		fetchNextPage,
	} = useInfiniteAgentRuns({ pageSize: 50 });
	const rerun = useRerunAgentRun();

	// Subscribe to real-time updates; the hook patches the shared
	// ["agent-runs", ...] cache in place so new runs prepend and in-progress
	// status changes (queued → running → completed) reflect live.
	useAgentRunListStream({ enabled: true });

	const runs: AgentRun[] = (data?.pages.flatMap((p) => p.items) ??
		[]) as AgentRun[];

	function handleRerun(runId: string) {
		rerun.mutate(
			{ params: { path: { run_id: runId } } },
			{
				onSuccess: (data) => {
					toast.success("Rerun queued");
					if (data.run_id) {
						// We don't know the agent_id from the response — find it
						// from the source run we clicked.
						const source = runs.find((r) => r.id === runId);
						if (source) {
							navigate(
								`/agents/${source.agent_id}/runs/${data.run_id}`,
								{ state: runNavigationState },
							);
						}
					}
				},
				onError: () => toast.error("Failed to queue rerun"),
			},
		);
	}

	if (isLoading) {
		return (
			<div className="space-y-2" data-testid="agent-runs-panel-loading">
				{[...Array(5)].map((_, i) => (
					<Skeleton key={i} className="h-10 w-full" />
				))}
			</div>
		);
	}

	if (runs.length === 0) {
		return (
			<div
				className="rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10 p-8 text-center text-sm text-muted-foreground"
				data-testid="agent-runs-panel-empty"
			>
				<Bot className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
				No agent runs yet.
			</div>
		);
	}

	return (
		<div data-testid="agent-runs-panel">
			<DataTable>
				<DataTableHeader>
					<DataTableRow>
						<DataTableHead>Agent</DataTableHead>
						<DataTableHead>Asked</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap text-right">
							Duration
						</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap">Verdict</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap">Started</DataTableHead>
						<DataTableHead className="w-0 whitespace-nowrap"></DataTableHead>
					</DataTableRow>
				</DataTableHeader>
				<DataTableBody>
					{runs.map((run) => (
						<DataTableRow
							key={run.id}
							className="cursor-pointer hover:bg-accent/40"
							onClick={() =>
								navigate(
									`/agents/${run.agent_id}/runs/${run.id}`,
									{ state: runNavigationState },
								)
							}
						>
							<DataTableCell>
								<div className="flex items-center gap-2">
									<Bot className="h-3.5 w-3.5 text-muted-foreground" />
									<span className="font-medium">
										{run.agent_name ?? "Agent"}
									</span>
								</div>
							</DataTableCell>
							<DataTableCell className="max-w-md truncate">
								{run.asked || run.did || "—"}
							</DataTableCell>
							<DataTableCell className="w-0 whitespace-nowrap">
								<RunStatusBadge status={run.status} />
							</DataTableCell>
							<DataTableCell className="w-0 whitespace-nowrap text-right tabular-nums">
								{run.duration_ms != null
									? formatDuration(run.duration_ms)
									: "—"}
							</DataTableCell>
							<DataTableCell className="w-0 whitespace-nowrap">
								<VerdictGlyph verdict={run.verdict} />
							</DataTableCell>
							<DataTableCell className="w-0 whitespace-nowrap text-xs text-muted-foreground">
								<span className="inline-flex items-center gap-1">
									<Clock className="h-3 w-3" />
									{run.started_at
										? formatDate(run.started_at)
										: "—"}
								</span>
							</DataTableCell>
							<DataTableCell
								className="w-0 whitespace-nowrap"
								onClick={(e) => e.stopPropagation()}
							>
								<Button
									type="button"
									size="sm"
									variant="ghost"
									data-testid={`rerun-${run.id}`}
									disabled={rerun.isPending}
									onClick={() => handleRerun(run.id)}
									title="Rerun with the same input"
								>
									<RefreshCw className="h-3.5 w-3.5" />
								</Button>
							</DataTableCell>
						</DataTableRow>
					))}
				</DataTableBody>
			</DataTable>
			<InfiniteScrollSentinel
				hasNext={!!hasNextPage}
				isLoading={isFetchingNextPage}
				onLoadMore={() => fetchNextPage()}
			/>
		</div>
	);
}

export default AgentRunsPanel;
