import { ListLoadError } from "@/components/layout/ListLoadError";
/**
 * AgentRunsPanel — cross-agent runs table rendered inside ExecutionHistory
 * when the page is switched to the agents tab (`/history?type=agents`).
 *
 * Fleet-wide agent run history with the same filter and pagination shell as
 * workflow history.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useSearchParams,
} from "react-router-dom";
import { toast } from "sonner";
import { Bot, Clock, RefreshCw } from "lucide-react";

import { DateRangePicker } from "@/components/ui/date-range-picker";
import { MarkdownContent } from "@/components/common/MarkdownContent";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { useAuth } from "@/contexts/AuthContext";
import { useAgents } from "@/hooks/useAgents";
import { AgentRunRecord, RunStatusBadge, VerdictGlyph } from "./AgentRunRecord";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { PaginationFooter } from "@/components/pagination/PaginationFooter";
import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
	DataTableFooter,
} from "@/components/ui/data-table";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
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
import type { DateRange } from "react-day-picker";
import type { components } from "@/lib/v1";

type AgentRun = components["schemas"]["AgentRunResponse"];
const PAGE_SIZE = 25;
const ALL_FILTER_VALUE = "__all__";
const AGENT_RUN_STATUSES = [
	{ value: "completed", label: "Completed" },
	{ value: "running", label: "Running" },
	{ value: "queued", label: "Queued" },
	{ value: "failed", label: "Failed" },
	{ value: "cancelled", label: "Cancelled" },
	{ value: "budget_exceeded", label: "Budget exceeded" },
] as const;

export function AgentRunsPanel() {
	const isDesktop = useIsDesktop();
	const navigate = useNavigate();
	const location = useLocation();
	const [searchParams, setSearchParams] = useSearchParams();
	const { isPlatformAdmin } = useAuth();
	const agentIdFilter = searchParams.get("agent") || "";
	const statusFilter = searchParams.get("status") || "";
	const [searchTerm, setSearchTerm] = useState(searchParams.get("q") || "");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [dateRange, setDateRange] = useState<DateRange | undefined>();
	const [filtersOpen, setFiltersOpen] = useState(false);
	const [pageIndex, setPageIndex] = useState(0);
	const { data: agents } = useAgents(
		isPlatformAdmin ? filterOrgId : undefined,
		{ includeInactive: true },
	);
	const runNavigationState = createAgentRunNavigationState({
		href: getLocationHref(location),
		label: "Back to run history",
	});
	const listFilters = useMemo(() => {
		const params: Parameters<typeof useInfiniteAgentRuns>[0] = {
			pageSize: PAGE_SIZE,
		};
		if (agentIdFilter) params.agentId = agentIdFilter;
		if (statusFilter) params.status = statusFilter;
		if (isPlatformAdmin && filterOrgId) params.orgId = filterOrgId;
		if (searchTerm) params.q = searchTerm;
		if (dateRange?.from) {
			const startDate = new Date(dateRange.from);
			startDate.setHours(0, 0, 0, 0);
			const endDate = new Date(dateRange.to || dateRange.from);
			endDate.setHours(23, 59, 59, 999);
			params.startDate = startDate.toISOString();
			params.endDate = endDate.toISOString();
		}
		return params;
	}, [
		agentIdFilter,
		dateRange,
		filterOrgId,
		isPlatformAdmin,
		searchTerm,
		statusFilter,
	]);
	const {
		data,
		isLoading,
		isError,
		isFetching,
		refetch,
		hasNextPage,
		isFetchingNextPage,
		isFetchNextPageError,
		fetchNextPage,
	} = useInfiniteAgentRuns(listFilters);
	const rerun = useRerunAgentRun();
	const [pendingRunId, setPendingRunId] = useState<string | null>(null);
	const [rerunError, setRerunError] = useState<{
		id: string;
		name: string;
	} | null>(null);
	const rerunBusy = useRef(false);
	const rerunErrorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (rerunError) rerunErrorRef.current?.focus();
	}, [rerunError]);
	const isRerunning = rerun.isPending || pendingRunId !== null;

	// Subscribe to real-time updates; the hook patches the shared
	// ["agent-runs", ...] cache in place so new runs prepend and in-progress
	// status changes (queued → running → completed) reflect live.
	useAgentRunListStream({ enabled: true });

	const runs = (data?.pages[pageIndex]?.items ?? []) as AgentRun[];
	const total = data?.pages[0]?.total ?? 0;
	const hasActiveFilters =
		agentIdFilter !== "" ||
		statusFilter !== "" ||
		searchTerm !== "" ||
		dateRange !== undefined ||
		(isPlatformAdmin && filterOrgId !== undefined);
	const hasPreviousPage = pageIndex > 0;
	const hasFollowingPage = (pageIndex + 1) * PAGE_SIZE < total;
	const agentRunsPaginationFooter = (className?: string) => (
		<PaginationFooter
			aria-label="Agent run pages"
			className={className}
			summary={`${runs.length} run${runs.length === 1 ? "" : "s"} on this page · Page ${pageIndex + 1}`}
			pending={isFetchingNextPage}
			previousDisabled={!hasPreviousPage || isFetchingNextPage}
			nextDisabled={!hasFollowingPage || isFetchingNextPage}
			onPrevious={() => setPageIndex((current) => current - 1)}
			onNext={() => void handleNextPage()}
		/>
	);

	const filtersKey = `${agentIdFilter}|${statusFilter}|${searchTerm}|${dateRange?.from?.toISOString() ?? ""}|${dateRange?.to?.toISOString() ?? ""}|${filterOrgId ?? ""}`;
	const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey);
	if (prevFiltersKey !== filtersKey) {
		setPrevFiltersKey(filtersKey);
		setPageIndex(0);
	}

	function setQueryParam(name: string, value: string) {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (value) next.set(name, value);
				else next.delete(name);
				return next;
			},
			{ replace: true },
		);
	}

	function handleSearchChange(value: string) {
		setSearchTerm(value);
		setQueryParam("q", value);
	}

	function clearFilters() {
		setSearchTerm("");
		setDateRange(undefined);
		setFilterOrgId(undefined);
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete("agent");
				next.delete("status");
				next.delete("q");
				return next;
			},
			{ replace: true },
		);
	}

	async function handleNextPage() {
		const nextPageIndex = pageIndex + 1;
		if (data?.pages[nextPageIndex]) {
			setPageIndex(nextPageIndex);
			return;
		}
		if (!hasNextPage || isFetchingNextPage) return;
		const result = await fetchNextPage();
		if (result.data?.pages[nextPageIndex]) {
			setPageIndex(nextPageIndex);
		}
	}

	function handleRerun(runId: string) {
		if (rerunBusy.current) return;
		const source = runs.find((run) => run.id === runId);
		if (!source) return;
		rerunBusy.current = true;
		setPendingRunId(runId);
		setRerunError(null);

		rerun.mutate(
			{ params: { path: { run_id: runId } } },
			{
				onSuccess: (data) => {
					rerunBusy.current = false;
					setPendingRunId(null);
					toast.success("Rerun queued");
					if (data.run_id) {
						// We don't know the agent_id from the response — find it
						// from the source run we clicked.
						// Source was captured before the request so paging cannot change its destination.
						if (source) {
							navigate(
								`/agents/${source.agent_id}/runs/${data.run_id}`,
								{ state: runNavigationState },
							);
						}
					}
				},
				onError: () => {
					rerunBusy.current = false;
					setPendingRunId(null);
					setRerunError({
						id: source.id,
						name: source.agent_name || "this agent",
					});
				},
			},
		);
	}

	const toolbar = (
		<ListToolbar className="shrink-0 items-stretch">
			<div className="flex w-full min-w-0 flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
				<div className="flex min-w-0 items-start gap-2 lg:flex-1 lg:basis-52">
					<SearchBox
						value={searchTerm}
						onChange={handleSearchChange}
						placeholder={
							isDesktop ? "Search agent runs…" : "Search runs…"
						}
						aria-label="Search agent runs"
						className="min-w-0 flex-1 lg:min-w-52"
					/>
					{!isDesktop && (
						<Button
							variant="outline"
							className="min-h-11 shrink-0 gap-2 px-3"
							aria-label={
								filtersOpen ? "Hide filters" : "Show filters"
							}
							aria-expanded={filtersOpen}
							aria-controls="agent-history-filters"
							onClick={() => setFiltersOpen((open) => !open)}
						>
							Filters
							{hasActiveFilters && (
								<span className="text-xs text-muted-foreground">
									Active
								</span>
							)}
						</Button>
					)}
				</div>
				<div
					id="agent-history-filters"
					data-testid="agent-history-filters"
					hidden={!isDesktop && !filtersOpen}
					className={
						isDesktop
							? "contents"
							: filtersOpen
								? "flex flex-col gap-3 rounded-[var(--bf-radius-surface)] border border-border bg-card p-3"
								: "hidden"
					}
				>
					<Select
						value={agentIdFilter || ALL_FILTER_VALUE}
						onValueChange={(value) =>
							setQueryParam(
								"agent",
								value === ALL_FILTER_VALUE ? "" : value,
							)
						}
					>
						<SelectTrigger
							aria-label="Agent"
							className="min-h-11 w-full min-w-0 lg:w-52"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent position="popper">
							<SelectItem
								value={ALL_FILTER_VALUE}
								className="min-h-11"
							>
								All agents
							</SelectItem>
							{(agents ?? []).map((agent) =>
								agent.id ? (
									<SelectItem
										key={agent.id}
										value={agent.id}
										className="min-h-11"
									>
										{agent.name}
									</SelectItem>
								) : null,
							)}
						</SelectContent>
					</Select>
					{isPlatformAdmin && (
						<div className="w-full min-w-0 lg:w-48">
							<OrganizationSelect
								value={filterOrgId}
								onChange={setFilterOrgId}
								showAll={true}
								showGlobal={false}
								placeholder="All organizations"
							/>
						</div>
					)}
					<Select
						value={statusFilter || ALL_FILTER_VALUE}
						onValueChange={(value) =>
							setQueryParam(
								"status",
								value === ALL_FILTER_VALUE ? "" : value,
							)
						}
					>
						<SelectTrigger
							aria-label="Run status"
							className="min-h-11 w-full min-w-0 lg:w-44"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent position="popper">
							<SelectItem
								value={ALL_FILTER_VALUE}
								className="min-h-11"
							>
								All statuses
							</SelectItem>
							{AGENT_RUN_STATUSES.map((status) => (
								<SelectItem
									key={status.value}
									value={status.value}
									className="min-h-11"
								>
									{status.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<DateRangePicker
						dateRange={dateRange}
						onDateRangeChange={setDateRange}
						className="w-full min-w-0 sm:w-auto lg:w-56"
					/>
					{hasActiveFilters && (
						<Button
							type="button"
							variant="ghost"
							className="min-h-11"
							onClick={clearFilters}
						>
							Clear filters
						</Button>
					)}
				</div>
			</div>
		</ListToolbar>
	);

	if (isLoading) {
		return (
			<>
				{toolbar}
				<div
					role="status"
					aria-label="Loading agent runs"
					className="space-y-2"
					data-testid="agent-runs-panel-loading"
				>
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-48 w-full xl:h-10" />
					))}
				</div>
			</>
		);
	}

	if (isError && !data) {
		return (
			<>
				{toolbar}
				<div
					role="alert"
					className="space-y-3 rounded-[var(--bf-radius-surface)] border border-border bg-card p-4"
				>
					<p className="font-medium">Couldn't load agent runs</p>
					<p className="text-sm text-muted-foreground">
						Try again to load recent runs.
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={isFetching}
						onClick={() => void refetch()}
					>
						{isFetching ? "Retrying…" : "Retry loading agent runs"}
					</Button>
				</div>
			</>
		);
	}

	const cachedError =
		isError && data && !isFetchNextPageError ? (
			<ListLoadError
				resource="agent runs"
				hasCachedData={runs.length > 0}
				isRetrying={isFetching}
				onRetry={() => void refetch()}
			/>
		) : null;
	if (runs.length === 0) {
		return (
			<>
				{toolbar}
				{cachedError}
				<div
					className="rounded-[var(--bf-radius-surface)] border border-border bg-card p-8 text-center text-sm text-muted-foreground"
					data-testid="agent-runs-panel-empty"
				>
					<Bot className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
					{hasActiveFilters
						? "No agent runs match your filters."
						: "No agent runs yet."}
					{hasActiveFilters && (
						<div className="mt-4">
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								onClick={clearFilters}
							>
								Clear filters
							</Button>
						</div>
					)}
				</div>
			</>
		);
	}

	return (
		<div
			className="flex min-h-0 min-w-0 flex-1 flex-col gap-4"
			data-testid="agent-runs-panel"
		>
			{toolbar}
			{cachedError}
			{isFetchNextPageError && (
				<div
					role="alert"
					className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 p-4 text-sm"
				>
					<p>
						Could not load the next page. You’re still on page{" "}
						{pageIndex + 1}.
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={isFetchingNextPage}
						onClick={() => void handleNextPage()}
					>
						Retry next page
					</Button>
				</div>
			)}
			{rerunError && (
				<div
					ref={rerunErrorRef}
					tabIndex={-1}
					role="alert"
					className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 p-4 text-sm"
				>
					<p className="text-destructive [overflow-wrap:anywhere]">
						Could not queue a rerun for {rerunError.name}. The
						original run is unchanged.
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						disabled={isRerunning}
						onClick={() => handleRerun(rerunError.id)}
					>
						Retry rerun
					</Button>
				</div>
			)}

			{!isDesktop ? (
				<div className="space-y-4 lg:min-h-0 lg:flex-1 lg:overflow-auto xl:overflow-visible">
					<ul
						aria-label="Agent run records"
						className="divide-y divide-border rounded-[var(--bf-radius-surface)] border border-border bg-card"
					>
						{runs.map((run) => (
							<AgentRunRecord
								key={run.id}
								run={run}
								navigationState={runNavigationState}
								isRerunning={isRerunning}
								onRerun={handleRerun}
							/>
						))}
					</ul>
					{total > 0 && agentRunsPaginationFooter()}
				</div>
			) : (
				<DataTable className="min-h-0 min-w-0 [&_table]:table-fixed">
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead className="w-full px-2 sm:w-40 sm:px-4 xl:w-1/4">
								Agent
							</DataTableHead>
							<DataTableHead className="hidden sm:table-cell">
								Asked
							</DataTableHead>
							<DataTableHead className="w-28 whitespace-nowrap px-2 sm:px-4">
								Status
							</DataTableHead>
							<DataTableHead className="hidden w-20 whitespace-nowrap text-right lg:table-cell">
								Duration
							</DataTableHead>
							<DataTableHead className="hidden w-24 whitespace-nowrap xl:table-cell">
								Verdict
							</DataTableHead>
							<DataTableHead className="hidden w-48 whitespace-nowrap xl:table-cell">
								Started
							</DataTableHead>
							<DataTableHead className="w-20 whitespace-nowrap px-2 sm:px-4"></DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{runs.map((run) => (
							<DataTableRow
								key={run.id}
								href={`/agents/${run.agent_id}/runs/${run.id}`}
								className="hover:bg-accent/40"
								onClick={() =>
									navigate(
										`/agents/${run.agent_id}/runs/${run.id}`,
										{ state: runNavigationState },
									)
								}
							>
								<DataTableCell className="min-w-0 overflow-hidden px-2 sm:px-4">
									<div className="flex min-w-0 items-center gap-2">
										<Bot className="h-3.5 w-3.5 text-muted-foreground" />
										<Link
											to={`/agents/${run.agent_id}/runs/${run.id}`}
											state={runNavigationState}
											onClick={(event) =>
												event.stopPropagation()
											}
											title={run.agent_name ?? "Agent"}
											className="flex min-h-11 min-w-0 items-center rounded-[var(--bf-radius-control)] font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
										>
											<span className="truncate">
												{run.agent_name ?? "Agent"}
											</span>
										</Link>
										<span className="xl:hidden">
											<VerdictGlyph
												verdict={run.verdict}
											/>
										</span>
									</div>
									<div className="mt-1 truncate text-xs text-muted-foreground sm:hidden">
										{run.asked || run.did ? (
											<MarkdownContent
												content={
													run.asked || run.did || ""
												}
												variant="preview"
												className="truncate"
											/>
										) : (
											"—"
										)}
									</div>
									<div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 overflow-hidden text-xs text-muted-foreground xl:hidden">
										<span className="inline-flex min-w-0 items-center gap-1">
											<Clock className="h-3 w-3 shrink-0" />
											<span className="truncate">
												{run.started_at
													? formatDate(run.started_at)
													: "—"}
											</span>
										</span>
										{run.duration_ms != null && (
											<span className="tabular-nums lg:hidden">
												{formatDuration(
													run.duration_ms,
												)}
											</span>
										)}
									</div>
								</DataTableCell>
								<DataTableCell className="hidden max-w-md truncate sm:table-cell">
									{run.asked || run.did ? (
										<MarkdownContent
											content={run.asked || run.did || ""}
											variant="preview"
											className="truncate"
										/>
									) : (
										"—"
									)}
								</DataTableCell>
								<DataTableCell className="w-28 whitespace-nowrap px-2 sm:px-4">
									<RunStatusBadge status={run.status} />
								</DataTableCell>
								<DataTableCell className="hidden w-0 whitespace-nowrap text-right tabular-nums lg:table-cell">
									{run.duration_ms != null
										? formatDuration(run.duration_ms)
										: "—"}
								</DataTableCell>
								<DataTableCell className="hidden w-24 whitespace-nowrap xl:table-cell">
									<VerdictGlyph verdict={run.verdict} />
								</DataTableCell>
								<DataTableCell className="hidden w-0 whitespace-nowrap text-xs text-muted-foreground xl:table-cell">
									<span className="inline-flex items-center gap-1">
										<Clock className="h-3 w-3" />
										{run.started_at
											? formatDate(run.started_at)
											: "—"}
									</span>
								</DataTableCell>
								<DataTableCell
									className="w-11 whitespace-nowrap px-2 sm:w-0 sm:px-4"
									onClick={(e) => e.stopPropagation()}
								>
									<Button
										type="button"
										size="icon-lg"
										variant="ghost"
										data-testid={`rerun-${run.id}`}
										disabled={isRerunning}
										onClick={() => handleRerun(run.id)}
										title="Rerun with the same input"
									>
										<RefreshCw className="h-3.5 w-3.5" />
									</Button>
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
					{total > 0 && (
						<DataTableFooter>
							<DataTableRow>
								<DataTableCell colSpan={7} className="p-0">
									{agentRunsPaginationFooter("px-6")}
								</DataTableCell>
							</DataTableRow>
						</DataTableFooter>
					)}
				</DataTable>
			)}
		</div>
	);
}

export default AgentRunsPanel;
