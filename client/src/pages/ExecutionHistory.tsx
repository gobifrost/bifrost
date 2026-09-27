import { PageScrollArea } from "@/components/layout/PageWorkspace";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { ExecutionCancelAction } from "./ExecutionHistory/components/ExecutionCancelAction";
import { ExecutionCleanupDialog } from "./ExecutionHistory/components/ExecutionCleanupDialog";
import { useState, useMemo, useEffect, useRef, Fragment } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useSearchParams,
} from "react-router-dom";
import {
	RefreshCw,
	History as HistoryIcon,
	Globe,
	AlertCircle,
	SearchX,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { LogsView } from "./ExecutionHistory/components/LogsView";
import { ExecutionRecord } from "./ExecutionHistory/components/ExecutionRecord";
import { ExecutionDrawer } from "./ExecutionHistory/components/ExecutionDrawer";
import { ExecutionPreviewPanel } from "./ExecutionHistory/components/ExecutionPreviewPanel";
import { RunStatusBadge } from "@/components/execution";
import {
	formatRunDuration,
	formatRunTime,
	groupExecutionsByDay,
	runAnchorDate,
	summarizeRuns,
} from "./ExecutionHistory/components/historyView";
import { FAILURE_STATUSES } from "@/lib/execution-buckets";

import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { AgentRunsPanel } from "@/components/agents/AgentRunsPanel";
import { Bot as BotIcon, Workflow as WorkflowIcon } from "lucide-react";
import { useExecutions } from "@/hooks/useExecutions";
import { useExecutionHistory } from "@/hooks/useExecutionStream";
import { WorkflowSelector } from "@/components/forms/WorkflowSelector";
import { useScopeStore } from "@/stores/scopeStore";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { formatDate } from "@/lib/utils";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import type { ExecutionFilters } from "@/lib/client-types";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { PaginationFooter } from "@/components/pagination/PaginationFooter";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import type { DateRange } from "react-day-picker";
import type { components } from "@/lib/v1";
import {
	createExecutionHistoryRestoreState,
	readExecutionHistoryRestore,
} from "./ExecutionHistory/navigation";

type Organization = components["schemas"]["OrganizationPublic"];
type ExecutionStatus =
	components["schemas"]["ExecutionStatus"] | "Cancelling" | "Cancelled";

/** Statuses with a tab on this page — accepted via the `?status=` param. */
const STATUS_TABS: ExecutionStatus[] = [
	"Success",
	"Running",
	"Failed",
	"Pending",
	"Scheduled",
];

const LOG_LEVELS = new Set(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]);
const HISTORY_RETURN_STORAGE_KEY = "bifrost.execution-history.return";

function parseDateParam(value: string | null): Date | undefined {
	if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
	const date = new Date(`${value}T00:00:00`);
	return Number.isNaN(date.getTime()) ? undefined : date;
}

function formatDateParam(date: Date): string {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, "0");
	const day = String(date.getDate()).padStart(2, "0");
	return `${year}-${month}-${day}`;
}

function readSavedHistoryOrigin() {
	try {
		const saved = sessionStorage.getItem(HISTORY_RETURN_STORAGE_KEY);
		if (!saved) return null;
		return readExecutionHistoryRestore({
			executionHistoryRestore: JSON.parse(saved),
		});
	} catch {
		return null;
	}
}

function historySearch(href: string): string {
	return new URL(href, "https://bifrost.local").search;
}

export function ExecutionHistory() {
	const isDesktop = useIsDesktop();
	const queryClient = useQueryClient();
	const agentRunsFetching = useIsFetching({
		queryKey: ["agent-runs-infinite"],
	});
	const logsFetching = useIsFetching({
		queryKey: ["get", "/api/executions/logs"],
	});
	const [filtersOpen, setFiltersOpen] = useState(false);
	const [searchParams, setSearchParams] = useSearchParams();
	const location = useLocation();
	const navigate = useNavigate();
	const historyScrollRef = useRef<HTMLDivElement>(null);
	const restoreOrigin = useMemo(
		() => readExecutionHistoryRestore(location.state),
		[location.state],
	);
	const [savedHistoryOrigin] = useState(() =>
		location.search || restoreOrigin ? null : readSavedHistoryOrigin(),
	);
	const [restorePaginationSearch, setRestorePaginationSearch] = useState<
		string | null
	>(
		(restoreOrigin ?? savedHistoryOrigin)
			? historySearch((restoreOrigin ?? savedHistoryOrigin)!.href)
			: null,
	);
	const [restoredScrollOrigin, setRestoredScrollOrigin] =
		useState<typeof restoreOrigin>(null);
	const updateSearchParams = (update: (next: URLSearchParams) => void) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				update(next);
				return next;
			},
			{ replace: true },
		);
	};
	const { isPlatformAdmin, user } = useAuth();
	const orgParam = searchParams.get("org");
	const filterOrgId = orgParam === "global" ? null : orgParam || undefined;
	const setFilterOrgId = (value: string | null | undefined) => {
		updateSearchParams((next) => {
			if (value === undefined) next.delete("org");
			else next.set("org", value ?? "global");
		});
	};
	const workflowIdFilter = searchParams.get("workflow") || "";
	// `?status=` is the single source of truth for the status tab — derived
	// from the URL and written back through setSearchParams (same pattern as
	// `historyType` below), so tab changes survive refresh/back and Clear
	// filters can't leave a stale param behind.
	const statusParam = searchParams.get("status")?.toLowerCase();
	const statusFilter: ExecutionStatus | "all" =
		STATUS_TABS.find((s) => s.toLowerCase() === statusParam) ?? "all";
	const setStatusFilter = (value: ExecutionStatus | "all") => {
		updateSearchParams((next) => {
			if (value === "all") next.delete("status");
			else next.set("status", value);
		});
	};
	const searchTerm = searchParams.get("q") ?? "";
	const setSearchTerm = (value: string) => {
		updateSearchParams((next) => {
			if (value) next.set("q", value);
			else next.delete("q");
		});
	};
	const from = parseDateParam(searchParams.get("from"));
	const to = parseDateParam(searchParams.get("to"));
	const dateRange = from ? { from, to } : undefined;
	const setDateRange = (value: DateRange | undefined) => {
		updateSearchParams((next) => {
			if (value?.from) next.set("from", formatDateParam(value.from));
			else next.delete("from");
			if (value?.to) next.set("to", formatDateParam(value.to));
			else next.delete("to");
		});
	};
	const viewMode =
		searchParams.get("view") === "logs" ? "logs" : "executions";
	const setViewMode = (value: "executions" | "logs") => {
		updateSearchParams((next) => {
			if (value === "logs") next.set("view", "logs");
			else next.delete("view");
		});
	};
	const historyType = (
		searchParams.get("type") === "agents" ? "agents" : "workflows"
	) as "workflows" | "agents";
	const logLevelParam = searchParams.get("level");
	const logLevelFilter =
		logLevelParam && LOG_LEVELS.has(logLevelParam) ? logLevelParam : "all";
	const setLogLevelFilter = (value: string) => {
		updateSearchParams((next) => {
			if (value === "all") next.delete("level");
			else next.set("level", value);
		});
	};
	const selectedExecutionId = searchParams.get("execution");
	const setSelectedExecutionId = (value: string | null) => {
		updateSearchParams((next) => {
			if (value) next.set("execution", value);
			else next.delete("execution");
		});
	};
	// IDs that were optimistically flipped to Cancelled after a successful 200.
	const [optimisticCancelledIds, setOptimisticCancelledIds] = useState<
		Set<string>
	>(new Set());
	const isGlobalScope = useScopeStore((state) => state.isGlobalScope);
	const orgId = useScopeStore((state) => state.scope.orgId);

	// Enable real-time updates for history page
	// Platform admins subscribe to GLOBAL channel, regular users to their own channel
	const scope = isGlobalScope ? "GLOBAL" : orgId || "GLOBAL";
	useExecutionHistory({
		scope,
		enabled: true,
		isPlatformAdmin,
		userId: user?.id,
	});
	// Pagination state - stack of continuation tokens for "back" navigation
	const [pageStack, setPageStack] = useState<(string | null)[]>(
		() => (restoreOrigin ?? savedHistoryOrigin)?.pageStack ?? [],
	);
	const [currentToken, setCurrentToken] = useState<string | undefined>(
		() => (restoreOrigin ?? savedHistoryOrigin)?.currentToken,
	);

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	// Build filters including date range and local executions toggle.
	// The Failed tab means the whole failure group (Failed, Timeout, Stuck,
	// CompletedWithErrors) — the same set the dashboard's "N failed" link
	// counts — so it sends the comma-separated group to the server's
	// match-any status filter instead of a single exact status.
	const filters = useMemo(() => {
		const baseFilters: Record<string, string | boolean> = {};
		if (statusFilter === "Failed") {
			baseFilters.status = Array.from(FAILURE_STATUSES).join(",");
		} else if (statusFilter !== "all") {
			baseFilters.status = statusFilter;
		}

		// Retain the default exclusion for legacy local-runner records.
		baseFilters.excludeLocal = true;

		// Workflow IDs are implementation details; only admins get this filter.
		if (isPlatformAdmin && workflowIdFilter) {
			baseFilters.workflow_id = workflowIdFilter;
		}

		if (dateRange?.from) {
			// Set start to beginning of day (00:00:00)
			const startDate = new Date(dateRange.from);
			startDate.setHours(0, 0, 0, 0);

			// Set end to end of day (23:59:59.999)
			// If no end date selected, use the same day as start
			const endDate = new Date(dateRange.to || dateRange.from);
			endDate.setHours(23, 59, 59, 999);

			return {
				...baseFilters,
				start_date: startDate.toISOString(),
				end_date: endDate.toISOString(),
			};
		}

		return baseFilters;
	}, [statusFilter, dateRange, isPlatformAdmin, workflowIdFilter]);

	// Pass filterOrgId to backend for filtering (undefined = all, null = global only)
	// For platform admins, undefined means show all. For non-admins, backend handles filtering.
	const {
		data: response,
		isFetching,
		isError,
		refetch,
	} = useExecutions(
		isPlatformAdmin ? filterOrgId : undefined,
		filters as ExecutionFilters,
		currentToken,
		{ preservePageData: true },
	);

	// Memoize executions to prevent dependency issues
	const executions = useMemo(
		() => response?.executions || [],
		[response?.executions],
	);
	const nextToken = response?.continuation_token || null;
	const historyRefreshing =
		historyType === "agents"
			? agentRunsFetching > 0
			: viewMode === "logs"
				? logsFetching > 0
				: isFetching;
	const hasMore = nextToken !== null;

	const handlePreviewExecution = (execution_id: string) => {
		setSelectedExecutionId(execution_id);
	};

	const handleCancelled = (id: string, scheduled: boolean) => {
		if (scheduled)
			setOptimisticCancelledIds((previous) => new Set([...previous, id]));
		void refetch();
	};

	// Apply search filter
	const filteredExecutions = useSearch(executions, searchTerm, [
		"workflow_name",
		"executed_by_name",
		"execution_id",
		(exec) => exec.status,
	]);

	// Pagination handlers
	const handleNextPage = () => {
		if (nextToken) {
			// Push current state to stack for "back" navigation
			setPageStack([...pageStack, currentToken || null]);
			setCurrentToken(nextToken);
		}
	};

	const handlePreviousPage = () => {
		if (pageStack.length > 0) {
			// Pop from stack to go back
			const newStack = [...pageStack];
			const previousToken = newStack.pop();
			setPageStack(newStack);
			setCurrentToken(previousToken || undefined);
		}
	};

	// Reset pagination when filters change. Adjust during render with a
	// previous-key sentinel rather than via setState-in-effect.
	const filtersKey = `${statusFilter}|${dateRange?.from?.toISOString() ?? ""}|${dateRange?.to?.toISOString() ?? ""}|${filterOrgId ?? ""}|${workflowIdFilter ?? ""}`;
	const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey);
	if (prevFiltersKey !== filtersKey) {
		setPrevFiltersKey(filtersKey);
		const restoringPagination = restorePaginationSearch === location.search;
		if (restoringPagination) setRestorePaginationSearch(null);
		if (!restoringPagination) {
			setPageStack([]);
			setCurrentToken(undefined);
		}
	}

	// Drop optimistic-cancel entries once the server echoes the row as
	// Cancelled (or it drops off the current page). Keeps the set from
	// growing forever. Adjust during render with a previous-executions-ref
	// sentinel rather than via setState-in-effect.
	const [prevExecutionsRef, setPrevExecutionsRef] = useState(executions);
	if (prevExecutionsRef !== executions) {
		setPrevExecutionsRef(executions);
		if (optimisticCancelledIds.size > 0) {
			const visibleIds = new Set(executions.map((e) => e.execution_id));
			const next = new Set<string>();
			for (const id of optimisticCancelledIds) {
				const row = executions.find((e) => e.execution_id === id);
				if (!row) continue; // dropped off page → forget
				if (row.status === "Cancelled") continue; // server caught up
				if (visibleIds.has(id)) next.add(id);
			}
			if (next.size !== optimisticCancelledIds.size) {
				setOptimisticCancelledIds(next);
			}
		}
	}

	// Whether any narrowing filter is active — drives which empty state shows.
	const hasActiveFilters =
		searchTerm !== "" ||
		statusFilter !== "all" ||
		dateRange !== undefined ||
		(isPlatformAdmin &&
			(workflowIdFilter !== "" || filterOrgId !== undefined));

	const handleClearFilters = () => {
		updateSearchParams((next) => {
			for (const key of [
				"q",
				"from",
				"to",
				"org",
				"workflow",
				"status",
				"level",
			]) {
				next.delete(key);
			}
		});
	};

	// Header rollup: page-level run counts. Honest about scope — these are
	// the runs currently loaded, with "more available" when paginated.
	const rollup = useMemo(() => summarizeRuns(executions), [executions]);

	// Day groups preserve server (newest-first) order.
	const dayGroups = useMemo(
		() => groupExecutionsByDay(filteredExecutions),
		[filteredExecutions],
	);

	// One source of truth for the column count (admins get the Org column).
	const previewOpen = isDesktop && selectedExecutionId !== null;
	const createHistoryOrigin = () => ({
		href: `${location.pathname}${location.search}`,
		scrollTop: isDesktop
			? (historyScrollRef.current?.scrollTop ??
				document.querySelector<HTMLElement>("[data-page-scroll]")
					?.scrollTop ??
				0)
			: window.scrollY,
		currentToken,
		pageStack,
	});
	useEffect(() => {
		if (!savedHistoryOrigin) return;
		navigate(savedHistoryOrigin.href, {
			replace: true,
			state: createExecutionHistoryRestoreState(savedHistoryOrigin),
		});
	}, [navigate, savedHistoryOrigin]);
	useEffect(() => {
		if (savedHistoryOrigin && !restoreOrigin) return;
		const save = () => {
			try {
				sessionStorage.setItem(
					HISTORY_RETURN_STORAGE_KEY,
					JSON.stringify(createHistoryOrigin()),
				);
			} catch {
				// Storage can be unavailable in private browsing; URL state still restores filters.
			}
		};
		save();
		if (!isDesktop) {
			window.addEventListener("scroll", save, { passive: true });
			return () => window.removeEventListener("scroll", save);
		}
		const scrollArea =
			historyScrollRef.current ??
			document.querySelector<HTMLElement>("[data-page-scroll]");
		if (!scrollArea) return;
		scrollArea.addEventListener("scroll", save, { passive: true });
		return () => scrollArea.removeEventListener("scroll", save);
	}, [isDesktop, location.search, restoreOrigin, savedHistoryOrigin]);
	useEffect(() => {
		if (
			!restoreOrigin ||
			isFetching ||
			restoredScrollOrigin === restoreOrigin
		)
			return;
		const frame = requestAnimationFrame(() => {
			if (!isDesktop) {
				window.scrollTo({
					top: restoreOrigin.scrollTop,
					behavior: "auto",
				});
				if (window.scrollY === restoreOrigin.scrollTop) {
					setRestoredScrollOrigin(restoreOrigin);
				}
			} else {
				const scrollArea =
					historyScrollRef.current ??
					document.querySelector<HTMLElement>("[data-page-scroll]");
				if (!scrollArea) return;
				scrollArea.scrollTop = restoreOrigin.scrollTop;
				if (scrollArea.scrollTop === restoreOrigin.scrollTop) {
					setRestoredScrollOrigin(restoreOrigin);
				}
			}
		});
		return () => cancelAnimationFrame(frame);
	}, [
		executions,
		isDesktop,
		isFetching,
		restoreOrigin,
		restoredScrollOrigin,
	]);
	const columnCount = previewOpen ? 4 : isPlatformAdmin ? 7 : 6;

	const showPaginationFooter = hasMore || pageStack.length > 0;
	const executionPageSummary = `${filteredExecutions.length} run${filteredExecutions.length !== 1 ? "s" : ""} on this page · Page ${pageStack.length + 1}`;

	const paginationFooter = showPaginationFooter && (
		<PaginationFooter
			aria-label="Execution pages"
			summary={executionPageSummary}
			pending={isFetching}
			previousDisabled={pageStack.length === 0 || isFetching}
			nextDisabled={!hasMore || isFetching}
			onPrevious={handlePreviousPage}
			onNext={handleNextPage}
		/>
	);

	const historyTypeToggle = isPlatformAdmin ? (
		<ToggleGroup
			type="single"
			value={historyType}
			onValueChange={(value: string) => {
				if (!value) return;
				updateSearchParams((next) => {
					if (value === "workflows") next.delete("type");
					else next.set("type", value);
				});
			}}
			aria-label="Execution history type"
			size="lg"
			className="grid w-full grid-cols-2 justify-start sm:flex sm:w-auto"
			data-testid="history-type-toggle"
		>
			<ToggleGroupItem
				value="workflows"
				aria-label="Workflows"
				className="gap-1.5"
			>
				<WorkflowIcon className="h-3.5 w-3.5" />
				Workflows
			</ToggleGroupItem>
			<ToggleGroupItem
				value="agents"
				aria-label="Agents"
				className="gap-1.5"
			>
				<BotIcon className="h-3.5 w-3.5" />
				Agents
			</ToggleGroupItem>
		</ToggleGroup>
	) : null;

	return (
		<section
			aria-labelledby="history-heading"
			className="mx-auto flex min-h-full lg:h-full lg:min-h-0 w-full max-w-7xl min-w-0 flex-col gap-4 pb-1"
		>
			<ListPageHeader
				className="shrink-0 flex-row flex-nowrap items-start gap-4"
				title={<span id="history-heading">History</span>}
				titleAccessory={isDesktop ? historyTypeToggle : null}
				description={
					<span data-testid="history-summary">
						{historyType === "agents" ? (
							"View agent run history across the fleet"
						) : viewMode === "logs" ? (
							"Search execution logs across workflows"
						) : rollup.total > 0 ? (
							<>
								{rollup.total} run
								{rollup.total !== 1 ? "s" : ""}
								{rollup.succeeded > 0 && (
									<> · {rollup.succeeded} succeeded</>
								)}
								{rollup.failed > 0 && (
									<>
										{" · "}
										<span className="font-medium text-destructive">
											{rollup.failed} failed
										</span>
									</>
								)}
								{rollup.running > 0 && (
									<> · {rollup.running} in progress</>
								)}
								{rollup.scheduled > 0 && (
									<> · {rollup.scheduled} scheduled</>
								)}
								{hasMore && <> · more available</>}
							</>
						) : (
							"Every workflow run — manual, scheduled, or form-triggered"
						)}
					</span>
				}
				actions={
					<>
						{isPlatformAdmin && (
							<ExecutionCleanupDialog
								onCleaned={() => void refetch()}
							/>
						)}

						<Button
							variant="outline"
							size="icon-lg"
							onClick={() =>
								historyType === "agents"
									? void queryClient.invalidateQueries({
											queryKey: ["agent-runs-infinite"],
										})
									: viewMode === "logs"
										? void queryClient.invalidateQueries({
												queryKey: [
													"get",
													"/api/executions/logs",
												],
											})
										: void refetch()
							}
							disabled={historyRefreshing}
							aria-label="Refresh execution history"
						>
							<RefreshCw
								className={`h-4 w-4 motion-reduce:animate-none ${historyRefreshing ? "animate-spin" : ""}`}
							/>
						</Button>
					</>
				}
				actionsClassName="shrink-0 self-start"
			/>
			{!isDesktop && historyTypeToggle}

			{historyType === "agents" ? <AgentRunsPanel /> : null}

			{historyType === "workflows" ? (
				<PageScrollArea
					ref={historyScrollRef}
					aria-label="Workflow history workspace"
					className="flex flex-col gap-3"
				>
					<ListToolbar className="items-stretch">
						<div className="flex w-full min-w-0 flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
							<div className="flex min-w-0 items-start gap-2 lg:flex-1 lg:basis-52">
								<SearchBox
									value={searchTerm}
									onChange={setSearchTerm}
									placeholder={
										viewMode === "logs"
											? isDesktop
												? "Search log messages..."
												: "Search logs…"
											: isDesktop
												? "Search runs…"
												: "Search runs…"
									}
									aria-label={
										viewMode === "logs"
											? "Search log messages..."
											: "Search by workflow name, user, or execution ID..."
									}
									className="min-w-0 flex-1 lg:min-w-52"
								/>
								{!isDesktop && (
									<Button
										variant="outline"
										className="min-h-11 shrink-0 gap-2 px-3"
										aria-label={
											filtersOpen
												? "Hide filters"
												: "Show filters"
										}
										aria-expanded={filtersOpen}
										aria-controls="history-filters"
										onClick={() =>
											setFiltersOpen((open) => !open)
										}
									>
										Filters
										{(workflowIdFilter ||
											filterOrgId !== undefined ||
											dateRange?.from) && (
											<span className="text-xs text-muted-foreground">
												Active
											</span>
										)}
									</Button>
								)}
							</div>

							<div className="flex min-w-0 flex-col gap-3 lg:contents">
								<div
									id="history-filters"
									data-testid="history-filters"
									hidden={!isDesktop && !filtersOpen}
									className={
										isDesktop
											? "contents"
											: filtersOpen
												? "flex flex-col gap-3 rounded-[var(--bf-radius-surface)] border border-border bg-card p-3"
												: "hidden"
									}
								>
									{isPlatformAdmin && (
										<WorkflowSelector
											value={
												workflowIdFilter || undefined
											}
											onChange={(value) => {
												const newFilter = value ?? "";
												updateSearchParams((next) => {
													if (newFilter)
														next.set(
															"workflow",
															newFilter,
														);
													else
														next.delete("workflow");
												});
											}}
											variant="combobox"
											allowClear={true}
											placeholder="All workflows"
											className="w-full min-w-0 lg:w-44"
										/>
									)}
									{isPlatformAdmin && (
										<div className="w-full min-w-0 lg:w-44">
											<OrganizationSelect
												value={filterOrgId}
												onChange={setFilterOrgId}
												showAll={true}
												showGlobal={true}
												placeholder="All organizations"
											/>
										</div>
									)}
									<DateRangePicker
										dateRange={dateRange}
										onDateRangeChange={setDateRange}
										className="w-full min-w-0 sm:w-auto lg:w-56"
									/>
								</div>
								<div
									data-testid="history-controls"
									className="flex flex-wrap items-center gap-3 lg:ml-auto"
								>
									{isPlatformAdmin && (
										<>
											<div className="flex items-center gap-2">
												<Switch
													id="view-mode"
													checked={
														viewMode === "logs"
													}
													onCheckedChange={(
														checked,
													) =>
														setViewMode(
															checked
																? "logs"
																: "executions",
														)
													}
												/>
												<Label
													htmlFor="view-mode"
													className="flex min-h-11 cursor-pointer items-center whitespace-nowrap text-sm font-normal text-muted-foreground"
												>
													Logs view
												</Label>
											</div>
										</>
									)}
								</div>
							</div>
						</div>
					</ListToolbar>

					{/* Status/Level Tabs and Content */}
					{viewMode === "logs" ? (
						<Tabs
							value={logLevelFilter}
							onValueChange={setLogLevelFilter}
							className="flex flex-col flex-1 min-h-0 lg:min-h-80"
						>
							{/* Log Level Tabs */}
							{!isDesktop ? (
								<Select
									value={logLevelFilter}
									onValueChange={setLogLevelFilter}
								>
									<SelectTrigger
										aria-label="Log level"
										className="w-full min-h-11"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent position="popper">
										<SelectItem
											value="all"
											className="min-h-11"
										>
											All levels
										</SelectItem>
										<SelectItem
											value="DEBUG"
											className="min-h-11"
										>
											Debug
										</SelectItem>
										<SelectItem
											value="INFO"
											className="min-h-11"
										>
											Info
										</SelectItem>
										<SelectItem
											value="WARNING"
											className="min-h-11"
										>
											Warning
										</SelectItem>
										<SelectItem
											value="ERROR"
											className="min-h-11"
										>
											Error
										</SelectItem>
										<SelectItem
											value="CRITICAL"
											className="min-h-11"
										>
											Critical
										</SelectItem>
									</SelectContent>
								</Select>
							) : (
								<div className="no-scrollbar w-fit max-w-full overflow-x-auto sm:overflow-visible">
									<TabsList className="w-max justify-start">
										<TabsTrigger value="all">
											All
										</TabsTrigger>
										<TabsTrigger value="DEBUG">
											Debug
										</TabsTrigger>
										<TabsTrigger value="INFO">
											Info
										</TabsTrigger>
										<TabsTrigger value="WARNING">
											Warning
										</TabsTrigger>
										<TabsTrigger value="ERROR">
											Error
										</TabsTrigger>
										<TabsTrigger value="CRITICAL">
											Critical
										</TabsTrigger>
									</TabsList>
								</div>
							)}

							{/* Logs View */}
							<LogsView
								workflowId={workflowIdFilter || undefined}
								filterOrgId={filterOrgId}
								dateRange={dateRange}
								searchTerm={searchTerm}
								logLevel={logLevelFilter}
							/>
						</Tabs>
					) : (
						<Tabs
							value={statusFilter}
							onValueChange={(v) =>
								setStatusFilter(v as ExecutionStatus | "all")
							}
							className="flex min-h-0 min-w-0 flex-1 flex-col lg:min-h-80"
						>
							{!isDesktop ? (
								<Select
									value={statusFilter}
									onValueChange={(value) =>
										setStatusFilter(
											value as ExecutionStatus | "all",
										)
									}
								>
									<SelectTrigger
										aria-label="Run status"
										className="w-full min-h-11"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent position="popper">
										<SelectItem
											value="all"
											className="min-h-11"
										>
											All statuses
										</SelectItem>
										<SelectItem
											value="Success"
											className="min-h-11"
										>
											Completed
										</SelectItem>
										<SelectItem
											value="Running"
											className="min-h-11"
										>
											Running
										</SelectItem>
										<SelectItem
											value="Failed"
											className="min-h-11"
										>
											Failed
										</SelectItem>
										<SelectItem
											value="Pending"
											className="min-h-11"
										>
											Pending
										</SelectItem>
										<SelectItem
											value="Scheduled"
											className="min-h-11"
										>
											Scheduled
										</SelectItem>
									</SelectContent>
								</Select>
							) : (
								<div className="no-scrollbar w-fit max-w-full overflow-x-auto sm:overflow-visible">
									<TabsList className="w-max justify-start">
										<TabsTrigger value="all">
											All
										</TabsTrigger>
										<TabsTrigger value="Success">
											Completed
										</TabsTrigger>
										<TabsTrigger value="Running">
											Running
										</TabsTrigger>
										<TabsTrigger value="Failed">
											Failed
										</TabsTrigger>
										<TabsTrigger value="Pending">
											Pending
										</TabsTrigger>
										<TabsTrigger value="Scheduled">
											Scheduled
										</TabsTrigger>
									</TabsList>
								</div>
							)}

							<TabsContent
								value={statusFilter}
								aria-label={
									!isDesktop ? "Execution results" : undefined
								}
								className="mt-4 flex-1 min-h-0 lg:flex lg:flex-col lg:overflow-hidden"
							>
								{isError ? (
									<div
										className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center"
										data-testid="history-error"
									>
										<AlertCircle className="h-10 w-10 text-destructive" />
										<h3 className="mt-4 text-lg font-semibold">
											Couldn't load execution history
										</h3>
										<p className="mt-2 text-sm text-muted-foreground">
											Something went wrong fetching runs.
											Your data is safe — try again.
										</p>
										<Button
											variant="outline"
											className="mt-4 min-h-11"
											onClick={() => refetch()}
										>
											<RefreshCw className="mr-2 h-4 w-4" />
											Try again
										</Button>
									</div>
								) : isFetching && !executions.length ? (
									<div
										className="overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 dark:ring-foreground/10"
										data-testid="history-loading"
									>
										<div className="border-b px-4 py-3">
											<Skeleton className="h-4 w-44" />
										</div>
										{Array.from({ length: 8 }).map(
											(_, i) => (
												<div
													key={i}
													className="flex items-center gap-6 border-b px-4 py-4 last:border-0"
												>
													<Skeleton className="h-4 w-48" />
													<Skeleton className="h-5 w-24 rounded-full" />
													<Skeleton className="h-4 w-28" />
													<Skeleton className="ml-auto h-4 w-20" />
													<Skeleton className="h-4 w-12" />
												</div>
											),
										)}
									</div>
								) : filteredExecutions.length > 0 ? (
									!isDesktop ? (
										<div
											className="space-y-6"
											aria-label="Execution records"
										>
											{dayGroups.map((group) => (
												<section
													key={group.key}
													className="space-y-2"
													aria-label={group.label}
												>
													<h2 className="py-2 text-sm font-semibold text-foreground">
														{group.label}
													</h2>
													<ul className="divide-y divide-border rounded-[var(--bf-radius-surface)] border border-border bg-card">
														{group.executions.map(
															(execution) => (
																<ExecutionRecord
																	key={
																		execution.execution_id
																	}
																	execution={
																		execution
																	}
																	status={
																		optimisticCancelledIds.has(
																			execution.execution_id,
																		)
																			? "Cancelled"
																			: execution.status
																	}
																	organizationName={
																		isPlatformAdmin
																			? execution.org_id
																				? getOrgName(
																						execution.org_id,
																					)
																				: "Global"
																			: undefined
																	}
																	onPreview={() =>
																		handlePreviewExecution(
																			execution.execution_id,
																		)
																	}
																	getOrigin={
																		createHistoryOrigin
																	}
																	actions={
																		<ExecutionCancelAction
																			executionId={
																				execution.execution_id
																			}
																			workflowName={
																				execution.workflow_name
																			}
																			status={
																				optimisticCancelledIds.has(
																					execution.execution_id,
																				)
																					? "Cancelled"
																					: execution.status
																			}
																			scheduledAt={
																				execution.scheduled_at
																			}
																			onCancelled={(
																				scheduled,
																			) =>
																				handleCancelled(
																					execution.execution_id,
																					scheduled,
																				)
																			}
																			onRefresh={() =>
																				void refetch()
																			}
																		/>
																	}
																/>
															),
														)}
													</ul>
												</section>
											))}
											{paginationFooter}
										</div>
									) : (
										<div
											className={
												previewOpen
													? "grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(34rem,1fr)_minmax(22rem,28rem)]"
													: "grid min-h-0 flex-1 gap-3"
											}
										>
											<div className="flex min-h-0 min-w-0 flex-col gap-3">
												<DataTable
													className="min-w-0"
													aria-busy={
														isFetching
															? "true"
															: undefined
													}
												>
													<DataTableHeader>
														<DataTableRow>
															{isPlatformAdmin &&
																!previewOpen && (
																	<DataTableHead className="hidden w-px xl:table-cell">
																		Organization
																	</DataTableHead>
																)}
															<DataTableHead
																className={
																	previewOpen
																		? "w-full min-w-64"
																		: "w-full"
																}
															>
																Workflow
															</DataTableHead>
															<DataTableHead className="w-px">
																Status
															</DataTableHead>
															{!previewOpen && (
																<DataTableHead className="hidden w-px xl:table-cell">
																	Run by
																</DataTableHead>
															)}
															<DataTableHead
																className={
																	previewOpen
																		? "hidden w-px whitespace-nowrap 2xl:table-cell"
																		: "hidden w-px lg:table-cell"
																}
															>
																Started
															</DataTableHead>
															<DataTableHead className="w-px whitespace-nowrap text-right">
																Duration
															</DataTableHead>

															{!previewOpen && (
																<DataTableHead className="w-px text-right"></DataTableHead>
															)}
														</DataTableRow>
													</DataTableHeader>
													<DataTableBody>
														{dayGroups.map(
															(group) => (
																<Fragment
																	key={
																		group.key
																	}
																>
																	<DataTableRow
																		className="border-b hover:bg-transparent"
																		data-testid="history-day-row"
																	>
																		<DataTableCell
																			colSpan={
																				columnCount
																			}
																			className="bg-muted py-3 text-sm font-semibold text-foreground"
																		>
																			{
																				group.label
																			}
																		</DataTableCell>
																	</DataTableRow>
																	{group.executions.map(
																		(
																			execution,
																		) => {
																			// Apply optimistic flip: if the user just
																			// confirmed cancel on this row, render it
																			// as Cancelled until refetch converges.
																			const displayStatus =
																				optimisticCancelledIds.has(
																					execution.execution_id,
																				)
																					? "Cancelled"
																					: execution.status;

																			const isGlobalExecution =
																				!execution.org_id;
																			const anchor =
																				runAnchorDate(
																					execution,
																				);
																			const anchorIso =
																				execution.started_at ??
																				execution.scheduled_at ??
																				execution.completed_at;
																			const duration =
																				formatRunDuration(
																					execution.started_at,
																					execution.completed_at,
																				);
																			const hasErrorDetail =
																				!!execution.error_message &&
																				(displayStatus ===
																					"Failed" ||
																					displayStatus ===
																						"Timeout" ||
																					displayStatus ===
																						"CompletedWithErrors");

																			return (
																				<DataTableRow
																					key={
																						execution.execution_id
																					}
																					data-testid="execution-row"
																					data-execution-id={
																						execution.execution_id
																					}
																					data-state={
																						selectedExecutionId ===
																						execution.execution_id
																							? "selected"
																							: undefined
																					}
																					clickable
																					href={`/history/${execution.execution_id}`}
																					onClick={() =>
																						handlePreviewExecution(
																							execution.execution_id,
																						)
																					}
																				>
																					{isPlatformAdmin &&
																						!previewOpen && (
																							<DataTableCell className="hidden w-px whitespace-nowrap text-sm text-muted-foreground xl:table-cell">
																								{isGlobalExecution ? (
																									<span className="inline-flex items-center gap-1.5">
																										<Globe className="h-3.5 w-3.5" />
																										Global
																									</span>
																								) : (
																									getOrgName(
																										execution.org_id,
																									)
																								)}
																							</DataTableCell>
																						)}
																					<DataTableCell
																						className={
																							previewOpen
																								? "w-full min-w-64 max-w-0 py-2"
																								: "w-full max-w-0"
																						}
																						data-testid="execution-workflow-cell"
																					>
																						<Link
																							to={`/history/${execution.execution_id}`}
																							onClick={(
																								event,
																							) => {
																								if (
																									isDesktop &&
																									!event.metaKey &&
																									!event.ctrlKey &&
																									!event.shiftKey &&
																									!event.altKey
																								) {
																									event.preventDefault();
																									handlePreviewExecution(
																										execution.execution_id,
																									);
																								}
																							}}
																							className="block truncate rounded-[var(--bf-radius-control)] font-mono text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
																						>
																							{
																								execution.workflow_name
																							}
																						</Link>
																						{hasErrorDetail && (
																							<div
																								className="mt-0.5 truncate text-xs text-destructive/90"
																								title={
																									execution.error_message ??
																									undefined
																								}
																							>
																								{
																									execution.error_message
																								}
																							</div>
																						)}
																						<div
																							className={
																								previewOpen
																									? "mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground"
																									: "mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground xl:hidden"
																							}
																						>
																							{isPlatformAdmin && (
																								<span>
																									{isGlobalExecution
																										? "Global"
																										: getOrgName(
																												execution.org_id,
																											)}
																								</span>
																							)}
																							<span
																								className={
																									previewOpen
																										? undefined
																										: "xl:hidden"
																								}
																							>
																								by{" "}
																								{
																									execution.executed_by_name
																								}
																							</span>
																							<span
																								className={
																									previewOpen
																										? "2xl:hidden"
																										: "lg:hidden"
																								}
																							>
																								{anchorIso
																									? formatRunTime(
																											anchorIso,
																										)
																									: "Not started"}
																							</span>
																							{!previewOpen && (
																								<span className="xl:hidden">
																									{duration ??
																										"No duration"}
																								</span>
																							)}
																						</div>
																					</DataTableCell>
																					<DataTableCell className="w-px whitespace-nowrap">
																						<RunStatusBadge
																							status={
																								displayStatus
																							}
																							scheduledAt={
																								execution.scheduled_at
																							}
																						/>
																					</DataTableCell>
																					{!previewOpen && (
																						<DataTableCell className="hidden w-px whitespace-nowrap text-sm text-muted-foreground xl:table-cell">
																							{
																								execution.executed_by_name
																							}
																						</DataTableCell>
																					)}
																					<DataTableCell
																						className={
																							previewOpen
																								? "hidden w-28 min-w-28 whitespace-nowrap text-sm tabular-nums text-muted-foreground 2xl:table-cell"
																								: "hidden w-28 min-w-28 whitespace-nowrap text-sm tabular-nums text-muted-foreground lg:table-cell"
																						}
																						title={
																							anchor
																								? formatDate(
																										anchor,
																									)
																								: undefined
																						}
																					>
																						{anchorIso
																							? formatRunTime(
																									anchorIso,
																								)
																							: "—"}
																					</DataTableCell>
																					<DataTableCell className="w-24 min-w-24 whitespace-nowrap text-right text-sm tabular-nums text-muted-foreground">
																						{duration ??
																							"—"}
																					</DataTableCell>

																					{!previewOpen && (
																						<DataTableCell className="w-px px-2 text-right">
																							<div className="flex min-w-11 items-center justify-end gap-1 lg:min-w-8">
																								<ExecutionCancelAction
																									compact
																									executionId={
																										execution.execution_id
																									}
																									workflowName={
																										execution.workflow_name
																									}
																									status={
																										optimisticCancelledIds.has(
																											execution.execution_id,
																										)
																											? "Cancelled"
																											: execution.status
																									}
																									scheduledAt={
																										execution.scheduled_at
																									}
																									onCancelled={(
																										scheduled,
																									) =>
																										handleCancelled(
																											execution.execution_id,
																											scheduled,
																										)
																									}
																									onRefresh={() =>
																										void refetch()
																									}
																								/>
																							</div>
																						</DataTableCell>
																					)}
																				</DataTableRow>
																			);
																		},
																	)}
																</Fragment>
															),
														)}
													</DataTableBody>
												</DataTable>
												{paginationFooter}
											</div>
											{previewOpen && (
												<ExecutionPreviewPanel
													executionId={
														selectedExecutionId
													}
													onClose={() =>
														setSelectedExecutionId(
															null,
														)
													}
													onExecutionChange={
														setSelectedExecutionId
													}
													getOrigin={
														createHistoryOrigin
													}
												/>
											)}
										</div>
									)
								) : hasActiveFilters ? (
									<div
										className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center"
										data-testid="history-empty-filtered"
									>
										<SearchX className="h-10 w-10 text-muted-foreground" />
										<h3 className="mt-4 text-lg font-semibold">
											No runs match your filters
										</h3>
										<p className="mt-2 text-sm text-muted-foreground">
											Try widening the date range or
											clearing the search and status
											filters.
										</p>
										<Button
											variant="outline"
											className="mt-4"
											onClick={handleClearFilters}
										>
											Clear filters
										</Button>
									</div>
								) : (
									<div
										className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center"
										data-testid="history-empty"
									>
										<HistoryIcon className="h-10 w-10 text-muted-foreground" />
										<h3 className="mt-4 text-lg font-semibold">
											No runs yet
										</h3>
										<p className="mt-2 max-w-sm text-sm text-muted-foreground">
											When a workflow runs — manually, on
											a schedule, or from a form — it
											shows up here with its status and
											timing.
										</p>
									</div>
								)}
							</TabsContent>
						</Tabs>
					)}
				</PageScrollArea>
			) : null}

			<ExecutionDrawer
				executionId={selectedExecutionId}
				open={!isDesktop && selectedExecutionId !== null}
				onOpenChange={(open) => {
					if (!open) setSelectedExecutionId(null);
				}}
				onExecutionChange={setSelectedExecutionId}
			/>
		</section>
	);
}
