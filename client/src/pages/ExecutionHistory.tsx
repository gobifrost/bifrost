import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
	CheckCircle,
	XCircle,
	Loader2,
	Clock,
	RefreshCw,
	History as HistoryIcon,
	Globe,
	Building2,
	Eraser,
	AlertCircle,
	Info,
	Eye,
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
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { LogsView } from "./ExecutionHistory/components/LogsView";
import { ExecutionDrawer } from "./ExecutionHistory/components/ExecutionDrawer";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";

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
import { ExecutionStatusBadge } from "@/components/execution/ExecutionStatusBadge";
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
	const [statusFilter, setStatusFilter] = useState<ExecutionStatus | "all">(
		"all",
	);
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

	// Build filters including date range and local executions toggle
	const filters = useMemo(() => {
		const baseFilters: Record<string, string | boolean> =
			statusFilter !== "all" ? { status: statusFilter as string } : {};

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

	const getStatusBadge = (status: ExecutionStatus) => {
		switch (status) {
			case "Success":
				return (
					<Badge variant="default" className="bg-green-500">
						<CheckCircle className="mr-1 h-3 w-3" />
						Completed
					</Badge>
				);
			case "Failed":
				return (
					<Badge variant="destructive">
						<XCircle className="mr-1 h-3 w-3" />
						Failed
					</Badge>
				);
			case "Running":
				return (
					<Badge variant="secondary">
						<Loader2 className="mr-1 h-3 w-3 animate-spin" />
						Running
					</Badge>
				);
			case "Pending":
				return (
					<Badge variant="outline">
						<Clock className="mr-1 h-3 w-3" />
						Pending
					</Badge>
				);
			case "Cancelling":
				return (
					<Badge
						variant="secondary"
						className="bg-orange-500 text-white"
					>
						<Loader2 className="mr-1 h-3 w-3 animate-spin" />
						Cancelling
					</Badge>
				);
			case "Cancelled":
				return (
					<Badge
						variant="outline"
						className="border-gray-500 text-gray-600 dark:text-gray-400"
					>
						<XCircle className="mr-1 h-3 w-3" />
						Cancelled
					</Badge>
				);
			case "Timeout":
				return (
					<Badge variant="destructive">
						<XCircle className="mr-1 h-3 w-3" />
						Timeout
					</Badge>
				);
			case "CompletedWithErrors":
				return (
					<Badge
						variant="outline"
						className="border-yellow-500 text-yellow-600 dark:text-yellow-500"
					>
						<AlertCircle className="mr-1 h-3 w-3" />
						Completed with Errors
					</Badge>
				);
			default:
				return <Badge variant="outline">Unknown</Badge>;
		}
	};

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
	const searchFilteredExecutions = useSearch(executions || [], searchTerm, [
		"workflow_name",
		"executed_by_name",
		"execution_id",
		(exec) => exec.status,
	]);

	const filteredExecutions = searchFilteredExecutions;

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
					>
						<RefreshCw
							className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
						/>
					</Button>
				</div>
			</div>
			<p className="-mt-4 text-muted-foreground">
				{historyType === "agents"
					? "View agent run history across the fleet"
					: "View and track workflow execution history"}
				{historyType === "workflows" && executions.length > 0 && (
					<span className="ml-2">
						· Showing {executions.length} execution
						{executions.length !== 1 ? "s" : ""}
						{hasMore && " (more available)"}
					</span>
				)}
			</p>

			{historyType === "agents" ? <AgentRunsPanel /> : null}

			{historyType === "workflows" ? (
			<>
			{/* Search and Filters */}
			<div className="flex items-center gap-4">
				{isPlatformAdmin && (
					<div className="flex items-center gap-2">
						<Switch
							id="view-mode"
							checked={viewMode === "logs"}
							onCheckedChange={(checked) =>
								setViewMode(checked ? "logs" : "executions")
							}
						/>
						<Label
							htmlFor="view-mode"
							className="text-sm font-normal cursor-pointer whitespace-nowrap"
						>
							Logs View
						</Label>
					</div>
				)}
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder={viewMode === "logs"
						? "Search by workflow name..."
						: "Search by workflow name, user, or execution ID..."}
					className="flex-1 max-w-2xl"
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
				<DateRangePicker
					dateRange={dateRange}
					onDateRangeChange={setDateRange}
				/>
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
							className="text-sm font-normal cursor-pointer whitespace-nowrap"
						>
							Show Local Executions
						</Label>
					</div>
				)}
				{isPlatformAdmin && (
					<div className="w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
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
					defaultValue="all"
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
					{isFetching && !executions.length ? (
						<div className="flex items-center justify-center py-12">
							<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
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
									<DataTableHead>Executed By</DataTableHead>
									<DataTableHead>Started At</DataTableHead>
									<DataTableHead>Completed At</DataTableHead>
									<DataTableHead>Duration</DataTableHead>
									<DataTableHead className="text-right"></DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{filteredExecutions.map((execution) => {
									const duration =
										execution.completed_at &&
										execution.started_at
											? Math.round(
													(new Date(
														execution.completed_at,
													).getTime() -
														new Date(
															execution.started_at,
														).getTime()) /
														1000,
												)
											: null;

									// Use actual org_id from execution to determine scope
									const isGlobalExecution = !execution.org_id;

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
												<DataTableCell>
													{isGlobalExecution ? (
														<Badge
															variant="default"
															className="text-xs"
														>
															<Globe className="mr-1 h-3 w-3" />
															Global
														</Badge>
													) : (
														<Badge
															variant="outline"
															className="text-xs"
														>
															<Building2 className="mr-1 h-3 w-3" />
															{getOrgName(
																execution.org_id,
															)}
														</Badge>
													)}
												</DataTableCell>
											)}
											<DataTableCell
												className="font-mono text-sm"
												data-testid="execution-workflow-cell"
											>
												<div className="flex items-center gap-2">
													{execution.workflow_name}
												</div>
											</DataTableCell>
											<DataTableCell>
												<div className="flex items-center gap-2">
													{isScheduled ||
													displayStatus ===
														"Cancelled" ? (
														<ExecutionStatusBadge
															status={
																displayStatus
															}
															scheduledAt={
																execution.scheduled_at
															}
														/>
													) : (
														getStatusBadge(
															displayStatus,
														)
													)}
													{execution.error_message && (
														<TooltipProvider>
															<Tooltip>
																<TooltipTrigger
																	asChild
																>
																	<Info className="h-4 w-4 text-destructive cursor-help" />
																</TooltipTrigger>
																<TooltipContent
																	side="right"
																	className="max-w-md bg-popover text-popover-foreground"
																>
																	<p className="text-sm">
																		{
																			execution.error_message
																		}
																	</p>
																</TooltipContent>
															</Tooltip>
														</TooltipProvider>
													)}
												</div>
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
											<DataTableCell className="text-sm">
												{execution.completed_at
													? formatDate(
															execution.completed_at,
														)
													: "-"}
											</DataTableCell>
											<DataTableCell className="text-sm text-muted-foreground">
												{duration !== null
													? `${duration}s`
													: "-"}
											</DataTableCell>
											<DataTableCell className="text-right">
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
													<Button
														variant="ghost"
														size="icon"
														onClick={(e) => {
															e.stopPropagation();
															handleViewDetails(
																execution.execution_id,
															);
														}}
														title="View Details"
													>
														<Eye className="h-4 w-4" />
													</Button>
												</div>
											</DataTableCell>
										</DataTableRow>
									);
								})}
							</DataTableBody>
							<DataTableFooter>
								<DataTableRow>
									<DataTableCell
										colSpan={isGlobalScope ? 8 : 7}
										className="p-0"
									>
										<div className="px-6 py-4 flex items-center justify-center">
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
						</DataTable>
					) : (
						<div className="flex flex-col items-center justify-center py-12 text-center">
							<HistoryIcon className="h-12 w-12 text-muted-foreground" />
							<h3 className="mt-4 text-lg font-semibold">
								{searchTerm
									? "No executions match your search"
									: "No executions found"}
							</h3>
							<p className="mt-2 text-sm text-muted-foreground">
								{searchTerm
									? "Try adjusting your search term or clear the filter"
									: "Execute a workflow to see it appear here"}
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
