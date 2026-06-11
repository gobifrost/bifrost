import { useState, useMemo, Fragment } from "react";
import { useSearchParams } from "react-router-dom";
import {
	CheckCircle,
	XCircle,
	Loader2,
	RefreshCw,
	History as HistoryIcon,
	Globe,
	Eraser,
	AlertCircle,
	SearchX,
	ChevronRight,
} from "lucide-react";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { LogsView } from "./ExecutionHistory/components/LogsView";
import { ExecutionDrawer } from "./ExecutionHistory/components/ExecutionDrawer";
import { RunStatusBadge } from "@/components/execution";
import {
	formatRunDuration,
	formatRunTime,
	groupExecutionsByDay,
	runAnchorDate,
	summarizeRuns,
} from "./ExecutionHistory/components/historyView";
import { FAILURE_STATUSES } from "@/lib/execution-buckets";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { AgentRunsPanel } from "@/components/agents/AgentRunsPanel";
import { Bot as BotIcon, Workflow as WorkflowIcon } from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useExecutions, cancelExecution } from "@/hooks/useExecutions";
import { useExecutionHistory } from "@/hooks/useExecutionStream";
import { WorkflowSelector } from "@/components/forms/WorkflowSelector";
import { useScopeStore } from "@/stores/scopeStore";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { formatDate } from "@/lib/utils";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import type { ExecutionFilters } from "@/lib/client-types";
import {
	Pagination,
	PaginationContent,
	PaginationItem,
	PaginationLink,
	PaginationNext,
	PaginationPrevious,
} from "@/components/ui/pagination";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import type { DateRange } from "react-day-picker";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];
type ExecutionStatus =
	| components["schemas"]["ExecutionStatus"]
	| "Cancelling"
	| "Cancelled";

/** Statuses with a tab on this page — accepted via the `?status=` param. */
const STATUS_TABS: ExecutionStatus[] = [
	"Success",
	"Running",
	"Failed",
	"Pending",
	"Scheduled",
];

interface StuckExecution {
	execution_id: string;
	workflow_name: string;
	org_id?: string | null;
	form_id?: string | null;
	executed_by: string;
	executed_by_name: string;
	status: string;
	started_at?: string | null;
	completed_at?: string | null;
	error_message?: string | null;
	return_value?: Record<string, unknown> | null;
	output?: Record<string, unknown> | null;
	logs_count?: number;
	variables?: Record<string, unknown> | null;
}

export function ExecutionHistory() {
	const [searchParams, setSearchParams] = useSearchParams();
	const { isPlatformAdmin, user } = useAuth();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [workflowIdFilter, setWorkflowIdFilter] = useState(
		searchParams.get("workflow") || "",
	);
	// `?status=` is the single source of truth for the status tab — derived
	// from the URL and written back through setSearchParams (same pattern as
	// `historyType` below), so tab changes survive refresh/back and Clear
	// filters can't leave a stale param behind.
	const statusParam = searchParams.get("status")?.toLowerCase();
	const statusFilter: ExecutionStatus | "all" =
		STATUS_TABS.find((s) => s.toLowerCase() === statusParam) ?? "all";
	const setStatusFilter = (value: ExecutionStatus | "all") => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (value === "all") {
					next.delete("status");
				} else {
					next.set("status", value);
				}
				return next;
			},
			{ replace: true },
		);
	};
	const [searchTerm, setSearchTerm] = useState("");
	const [dateRange, setDateRange] = useState<DateRange | undefined>();
	const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false);
	const [stuckExecutions, setStuckExecutions] = useState<StuckExecution[]>(
		[],
	);
	const [loadingStuck, setLoadingStuck] = useState(false);
	const [cleaningUp, setCleaningUp] = useState(false);
	const [showLocal, setShowLocal] = useState(false);
	const [viewMode, setViewMode] = useState<"executions" | "logs">("executions");
	const historyType = (searchParams.get("type") === "agents"
		? "agents"
		: "workflows") as "workflows" | "agents";
	const [logLevelFilter, setLogLevelFilter] = useState<string>("all");
	const [drawerExecutionId, setDrawerExecutionId] = useState<string | null>(null);
	const [drawerOpen, setDrawerOpen] = useState(false);
	// Target for the "cancel scheduled run" confirm dialog. Null = closed.
	const [scheduledCancelTarget, setScheduledCancelTarget] = useState<{
		execution_id: string;
		workflow_name: string;
		scheduled_at: string | null | undefined;
	} | null>(null);
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
	const [pageStack, setPageStack] = useState<(string | null)[]>([]);
	const [currentToken, setCurrentToken] = useState<string | undefined>(
		undefined,
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

		// Add excludeLocal filter (inverse of showLocal)
		baseFilters.excludeLocal = !showLocal;

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
	}, [statusFilter, dateRange, showLocal, isPlatformAdmin, workflowIdFilter]);

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
	);

	// Memoize executions to prevent dependency issues
	const executions = useMemo(
		() => response?.executions || [],
		[response?.executions],
	);
	const nextToken = response?.continuation_token || null;
	const hasMore = nextToken !== null;

	const handleViewDetails = (execution_id: string) => {
		setDrawerExecutionId(execution_id);
		setDrawerOpen(true);
	};

	const handleCancelExecution = async (
		execution_id: string,
		workflow_name: string,
	) => {
		try {
			await cancelExecution(execution_id);
			toast.success(`Cancellation requested for ${workflow_name}`);
			// Refetch to show updated status
			refetch();
		} catch (error) {
			toast.error(`Failed to cancel execution: ${error}`);
		}
	};

	// Cancel a SCHEDULED row via the workflows router. Different from the
	// generic cancel path above: Scheduled rows haven't been published yet, so
	// the cancel endpoint is a status-guarded UPDATE that returns 409 if the
	// promoter (or another caller) already moved it.
	const handleConfirmCancelScheduled = async () => {
		if (!scheduledCancelTarget) return;
		const { execution_id, workflow_name } = scheduledCancelTarget;
		setScheduledCancelTarget(null);
		try {
			const { data, error, response } = await apiClient.POST(
				"/api/workflows/executions/{execution_id}/cancel",
				{ params: { path: { execution_id } } },
			);
			if (error || (response && !response.ok)) {
				const status = response?.status;
				if (status === 409) {
					// Pull the current status out of the server's detail string
					// if possible, else fall back to a generic message.
					const detail =
						(error as { detail?: string } | undefined)?.detail ||
						"";
					const match = detail.match(/current status: (\w+)/i);
					const currentStatus = match ? match[1] : "already moved";
					toast.error(
						`Execution is ${currentStatus} — refreshing`,
					);
				} else {
					toast.error(
						`Failed to cancel ${workflow_name}: ${
							(error as { detail?: string } | undefined)?.detail ||
							"unknown error"
						}`,
					);
				}
				refetch();
				return;
			}
			// 200: optimistic flip to Cancelled so the row updates instantly,
			// and invalidate the list so we converge with the server.
			setOptimisticCancelledIds((prev) => {
				const next = new Set(prev);
				next.add(execution_id);
				return next;
			});
			toast.success(
				`Cancelled scheduled run of ${workflow_name}`,
			);
			void data; // data is { execution_id, status } — we already know both
			refetch();
		} catch (err) {
			toast.error(`Failed to cancel ${workflow_name}: ${err}`);
			refetch();
		}
	};

	const handleOpenCleanup = async () => {
		setCleanupDialogOpen(true);
		setLoadingStuck(true);

		try {
			const response = await apiClient.GET(
				"/api/executions/cleanup/stuck",
			);
			if (response.data) {
				setStuckExecutions(response.data.executions || []);
			}
		} catch {
			toast.error("Failed to load stuck executions");
		} finally {
			setLoadingStuck(false);
		}
	};

	const handleTriggerCleanup = async () => {
		setCleaningUp(true);

		try {
			const response = await apiClient.POST(
				"/api/executions/cleanup/trigger",
				{},
			);
			if (response.data) {
				toast.success(
					`Cleaned up ${response.data.cleaned} stuck executions`,
				);
				setCleanupDialogOpen(false);
				// Refetch executions to show updated status
				refetch();
			}
		} catch {
			toast.error("Failed to trigger cleanup");
		} finally {
			setCleaningUp(false);
		}
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
	const filtersKey = `${statusFilter}|${dateRange?.from?.toISOString() ?? ""}|${dateRange?.to?.toISOString() ?? ""}|${showLocal}|${filterOrgId ?? ""}|${workflowIdFilter ?? ""}`;
	const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey);
	if (prevFiltersKey !== filtersKey) {
		setPrevFiltersKey(filtersKey);
		setPageStack([]);
		setCurrentToken(undefined);
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
		(isPlatformAdmin && (workflowIdFilter !== "" || filterOrgId !== undefined));

	const handleClearFilters = () => {
		setSearchTerm("");
		setDateRange(undefined);
		setFilterOrgId(undefined);
		setWorkflowIdFilter("");
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete("workflow");
				next.delete("status");
				return next;
			},
			{ replace: true },
		);
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
	const columnCount = isPlatformAdmin ? 7 : 6;

	const showPaginationFooter = hasMore || pageStack.length > 0;

	return (
		<div className="h-full flex flex-col space-y-6">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-4">
					<h1 className="text-4xl font-extrabold tracking-tight">
						History
					</h1>
					{isPlatformAdmin ? (
						<ToggleGroup
							type="single"
							value={historyType}
							onValueChange={(value: string) => {
								if (!value) return;
								setSearchParams(
									(prev) => {
										const next = new URLSearchParams(prev);
										if (value === "workflows") {
											next.delete("type");
										} else {
											next.set("type", value);
										}
										return next;
									},
									{ replace: true },
								);
							}}
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
					) : null}
				</div>
				<div className="flex items-center gap-2">
					<Dialog
						open={cleanupDialogOpen}
						onOpenChange={setCleanupDialogOpen}
					>
						<DialogTrigger asChild>
							<Button
								variant="outline"
								size="icon"
								onClick={handleOpenCleanup}
								title="Cleanup Stuck Executions"
							>
								<Eraser className="h-4 w-4" />
							</Button>
						</DialogTrigger>
						<DialogContent className="max-w-3xl">
							<DialogHeader>
								<DialogTitle>
									Cleanup Stuck Executions
								</DialogTitle>
								<DialogDescription>
									Stuck executions are workflows that have
									been in Pending status for 10+ minutes or
									Running status for 30+ minutes.
								</DialogDescription>
							</DialogHeader>

							{loadingStuck ? (
								<div className="flex items-center justify-center py-12">
									<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
								</div>
							) : stuckExecutions.length === 0 ? (
								<div className="flex flex-col items-center justify-center py-12 text-center">
									<CheckCircle className="h-12 w-12 text-green-500 mb-4" />
									<h3 className="text-lg font-semibold">
										No Stuck Executions
									</h3>
									<p className="mt-2 text-sm text-muted-foreground">
										All executions are running normally
									</p>
								</div>
							) : (
								<DataTable>
									<DataTableHeader>
										<DataTableRow>
											<DataTableHead>
												Workflow
											</DataTableHead>
											<DataTableHead>
												Status
											</DataTableHead>
											<DataTableHead>
												Executed By
											</DataTableHead>
											<DataTableHead>
												Started At
											</DataTableHead>
										</DataTableRow>
									</DataTableHeader>
									<DataTableBody>
										{stuckExecutions.map((execution) => (
											<DataTableRow
												key={execution.execution_id}
											>
												<DataTableCell className="font-mono text-sm">
													{execution.workflow_name}
												</DataTableCell>
												<DataTableCell>
													<Badge
														variant={
															execution.status ===
															"Pending"
																? "outline"
																: "secondary"
														}
													>
														{execution.status}
													</Badge>
												</DataTableCell>
												<DataTableCell>
													{execution.executed_by_name}
												</DataTableCell>
												<DataTableCell className="text-sm">
													{execution.started_at
														? formatDate(
																execution.started_at,
															)
														: "-"}
												</DataTableCell>
											</DataTableRow>
										))}
									</DataTableBody>
								</DataTable>
							)}

							<DialogFooter>
								<Button
									variant="outline"
									onClick={() => setCleanupDialogOpen(false)}
								>
									Cancel
								</Button>
								<Button
									onClick={handleTriggerCleanup}
									disabled={
										stuckExecutions.length === 0 ||
										cleaningUp
									}
								>
									{cleaningUp && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									Cleanup {stuckExecutions.length} Execution
									{stuckExecutions.length !== 1 ? "s" : ""}
								</Button>
							</DialogFooter>
						</DialogContent>
					</Dialog>

					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						disabled={isFetching}
						title="Refresh"
					>
						<RefreshCw
							className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
						/>
					</Button>
				</div>
			</div>
			{/* Summary line: the rollup an operator actually scans for. */}
			<p
				className="-mt-4 text-sm text-muted-foreground"
				data-testid="history-summary"
			>
				{historyType === "agents" ? (
					"View agent run history across the fleet"
				) : rollup.total > 0 ? (
					<>
						{rollup.total} run{rollup.total !== 1 ? "s" : ""}
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
						{rollup.running > 0 && <> · {rollup.running} in progress</>}
						{rollup.scheduled > 0 && (
							<> · {rollup.scheduled} scheduled</>
						)}
						{hasMore && <> · more available</>}
					</>
				) : (
					"Every workflow run — manual, scheduled, or form-triggered"
				)}
			</p>

			{historyType === "agents" ? <AgentRunsPanel /> : null}

			{historyType === "workflows" ? (
			<>
			{/* Filters: search first and widest, entity filters grouped,
			    mode/debug toggles demoted to the end of the row. */}
			<div className="flex items-center gap-3">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder={viewMode === "logs"
						? "Search by workflow name..."
						: "Search by workflow name, user, or execution ID..."}
					className="flex-1 max-w-md"
				/>
				{isPlatformAdmin && (
					<WorkflowSelector
						value={workflowIdFilter || undefined}
						onChange={(value) => {
							const newFilter = value ?? "";
							setWorkflowIdFilter(newFilter);
							setSearchParams((prev) => {
								const next = new URLSearchParams(prev);
								if (newFilter) {
									next.set("workflow", newFilter);
								} else {
									next.delete("workflow");
								}
								return next;
							}, { replace: true });
						}}
						variant="combobox"
						allowClear={true}
						placeholder="All workflows"
						className="w-48"
					/>
				)}
				{isPlatformAdmin && (
					<div className="w-56">
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
				/>
				<div className="ml-auto flex items-center gap-3">
					{/* Show Local Executions - only for executions view */}
					{viewMode === "executions" && (
						<div className="flex items-center gap-2">
							<Checkbox
								id="show-local"
								checked={showLocal}
								onCheckedChange={(checked) =>
									setShowLocal(checked === true)
								}
							/>
							<Label
								htmlFor="show-local"
								className="text-sm font-normal cursor-pointer whitespace-nowrap text-muted-foreground"
							>
								Show local
							</Label>
						</div>
					)}
					{isPlatformAdmin && (
						<>
							<Separator
								orientation="vertical"
								className="h-5"
							/>
							<div className="flex items-center gap-2">
								<Switch
									id="view-mode"
									checked={viewMode === "logs"}
									onCheckedChange={(checked) =>
										setViewMode(
											checked ? "logs" : "executions",
										)
									}
								/>
								<Label
									htmlFor="view-mode"
									className="text-sm font-normal cursor-pointer whitespace-nowrap text-muted-foreground"
								>
									Logs view
								</Label>
							</div>
						</>
					)}
				</div>
			</div>

			{/* Status/Level Tabs and Content */}
			{viewMode === "logs" ? (
				<Tabs
					value={logLevelFilter}
					onValueChange={setLogLevelFilter}
					className="flex flex-col flex-1 min-h-0"
				>
					{/* Log Level Tabs */}
					<TabsList className="w-fit">
						<TabsTrigger value="all">All</TabsTrigger>
						<TabsTrigger value="DEBUG">Debug</TabsTrigger>
						<TabsTrigger value="INFO">Info</TabsTrigger>
						<TabsTrigger value="WARNING">Warning</TabsTrigger>
						<TabsTrigger value="ERROR">Error</TabsTrigger>
						<TabsTrigger value="CRITICAL">Critical</TabsTrigger>
					</TabsList>

					{/* Logs View */}
					<LogsView
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
					className="flex flex-col flex-1 min-h-0"
				>
				<TabsList className="w-fit">
					<TabsTrigger value="all">All</TabsTrigger>
					<TabsTrigger value="Success">Completed</TabsTrigger>
					<TabsTrigger value="Running">Running</TabsTrigger>
					<TabsTrigger value="Failed">Failed</TabsTrigger>
					<TabsTrigger value="Pending">Pending</TabsTrigger>
					<TabsTrigger value="Scheduled">Scheduled</TabsTrigger>
				</TabsList>

				<TabsContent
					value={statusFilter}
					className="mt-4 flex-1 min-h-0"
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
								Something went wrong fetching runs. Your data
								is safe — try again.
							</p>
							<Button
								variant="outline"
								className="mt-4"
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
							{Array.from({ length: 8 }).map((_, i) => (
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
							))}
						</div>
					) : filteredExecutions.length > 0 ? (
						<DataTable>
							<DataTableHeader>
								<DataTableRow>
									{isPlatformAdmin && (
										<DataTableHead>
											Organization
										</DataTableHead>
									)}
									<DataTableHead>Workflow</DataTableHead>
									<DataTableHead>Status</DataTableHead>
									<DataTableHead>Run by</DataTableHead>
									<DataTableHead>Started</DataTableHead>
									<DataTableHead className="text-right">
										Duration
									</DataTableHead>
									<DataTableHead className="text-right"></DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{dayGroups.map((group) => (
									<Fragment key={group.key}>
										<DataTableRow
											className="border-b hover:bg-transparent"
											data-testid="history-day-row"
										>
											<DataTableCell
												colSpan={columnCount}
												className="bg-muted/40 dark:bg-background/50 py-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
											>
												{group.label}
											</DataTableCell>
										</DataTableRow>
										{group.executions.map((execution) => {
											// Apply optimistic flip: if the user just
											// confirmed cancel on this row, render it
											// as Cancelled until refetch converges.
											const displayStatus =
												optimisticCancelledIds.has(
													execution.execution_id,
												)
													? "Cancelled"
													: execution.status;
											const isScheduled = displayStatus ===
												"Scheduled";
											const isGlobalExecution =
												!execution.org_id;
											const anchor = runAnchorDate(execution);
											const anchorIso =
												execution.started_at ??
												execution.scheduled_at ??
												execution.completed_at;
											const duration = formatRunDuration(
												execution.started_at,
												execution.completed_at,
											);
											const hasErrorDetail =
												!!execution.error_message &&
												(displayStatus === "Failed" ||
													displayStatus === "Timeout" ||
													displayStatus ===
														"CompletedWithErrors");

											return (
												<DataTableRow
													key={execution.execution_id}
													data-testid="execution-row"
													data-execution-id={
														execution.execution_id
													}
													clickable
													href={`/history/${execution.execution_id}`}
													onClick={(e) => {
														if (e.metaKey || e.ctrlKey || e.button === 1) return;
														handleViewDetails(
															execution.execution_id,
														);
													}}
												>
													{isPlatformAdmin && (
														<DataTableCell className="text-sm text-muted-foreground">
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
														className="max-w-md"
														data-testid="execution-workflow-cell"
													>
														<div className="truncate font-mono text-sm font-medium">
															{execution.workflow_name}
														</div>
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
													</DataTableCell>
													<DataTableCell>
														<RunStatusBadge
															status={displayStatus}
															scheduledAt={
																execution.scheduled_at
															}
														/>
													</DataTableCell>
													<DataTableCell className="text-sm text-muted-foreground">
														{execution.executed_by_name}
													</DataTableCell>
													<DataTableCell
														className="whitespace-nowrap text-sm text-muted-foreground"
														title={
															anchor
																? formatDate(anchor)
																: undefined
														}
													>
														{anchorIso
															? formatRunTime(
																	anchorIso,
																)
															: "—"}
													</DataTableCell>
													<DataTableCell className="whitespace-nowrap text-right text-sm tabular-nums text-muted-foreground">
														{duration ?? "—"}
													</DataTableCell>
													<DataTableCell className="w-px text-right">
														<div className="flex items-center justify-end gap-1">
															{(execution.status ===
																"Running" ||
																execution.status ===
																	"Pending") && (
																<Button
																	variant="ghost"
																	size="icon"
																	onClick={(e) => {
																		e.stopPropagation();
																		handleCancelExecution(
																			execution.execution_id,
																			execution.workflow_name,
																		);
																	}}
																	title="Cancel Execution"
																>
																	<XCircle className="h-4 w-4" />
																</Button>
															)}
															{isScheduled && (
																<Button
																	variant="ghost"
																	size="icon"
																	onClick={(e) => {
																		e.stopPropagation();
																		setScheduledCancelTarget(
																			{
																				execution_id:
																					execution.execution_id,
																				workflow_name:
																					execution.workflow_name,
																				scheduled_at:
																					execution.scheduled_at,
																			},
																		);
																	}}
																	title="Cancel scheduled execution"
																>
																	<XCircle className="h-4 w-4" />
																</Button>
															)}
															<ChevronRight
																className="h-4 w-4 text-muted-foreground/50"
																aria-hidden="true"
															/>
														</div>
													</DataTableCell>
												</DataTableRow>
											);
										})}
									</Fragment>
								))}
							</DataTableBody>
							{showPaginationFooter && (
								<DataTableFooter>
									<DataTableRow>
										<DataTableCell
											colSpan={columnCount}
											className="p-0"
										>
											<div className="px-6 py-3 flex items-center justify-center">
												<Pagination>
													<PaginationContent>
														<PaginationItem>
															<PaginationPrevious
																onClick={(e) => {
																	e.preventDefault();
																	handlePreviousPage();
																}}
																className={
																	pageStack.length ===
																		0 ||
																	isFetching
																		? "pointer-events-none opacity-50"
																		: "cursor-pointer"
																}
																aria-disabled={
																	pageStack.length ===
																		0 ||
																	isFetching
																}
															/>
														</PaginationItem>
														<PaginationItem>
															<PaginationLink
																isActive
															>
																{pageStack.length +
																	1}
															</PaginationLink>
														</PaginationItem>
														<PaginationItem>
															<PaginationNext
																onClick={(e) => {
																	e.preventDefault();
																	handleNextPage();
																}}
																className={
																	!hasMore ||
																	isFetching
																		? "pointer-events-none opacity-50"
																		: "cursor-pointer"
																}
																aria-disabled={
																	!hasMore ||
																	isFetching
																}
															/>
														</PaginationItem>
													</PaginationContent>
												</Pagination>
											</div>
										</DataTableCell>
									</DataTableRow>
								</DataTableFooter>
							)}
						</DataTable>
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
								Try widening the date range or clearing the
								search and status filters.
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
								When a workflow runs — manually, on a
								schedule, or from a form — it shows up here
								with its status and timing.
							</p>
						</div>
					)}
				</TabsContent>
				</Tabs>
			)}
			</>
			) : null}
			<AlertDialog
				open={!!scheduledCancelTarget}
				onOpenChange={(open) => {
					if (!open) setScheduledCancelTarget(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Cancel scheduled run?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Cancel scheduled run of{" "}
							<span className="font-mono">
								{scheduledCancelTarget?.workflow_name}
							</span>
							{scheduledCancelTarget?.scheduled_at
								? ` for ${new Date(
										scheduledCancelTarget.scheduled_at,
									).toLocaleString()}`
								: ""}
							? The workflow will not run.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Keep scheduled</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmCancelScheduled}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Confirm cancel
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<ExecutionDrawer
				executionId={drawerExecutionId}
				open={drawerOpen}
				onOpenChange={setDrawerOpen}
				onExecutionChange={setDrawerExecutionId}
			/>
		</div>
	);
}
