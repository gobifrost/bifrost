import { useEffect, type ReactNode } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useSearchParams,
} from "react-router-dom";
import {
	endOfDay,
	format,
	isValid,
	parse,
	startOfDay,
	subDays,
} from "date-fns";
import type { DateRange } from "react-day-picker";
import { AlertCircle } from "lucide-react";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { PaginationFooter } from "@/components/pagination/PaginationFooter";
import { SearchBox } from "@/components/search/SearchBox";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { RunStatusBadge } from "@/components/execution";
import { formatRunTime } from "./ExecutionHistory/components/historyView";
import { formatDate } from "@/lib/utils";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	useWorkflowResourceReport,
	type WorkflowResourceRun,
	type WorkflowResourceStatus,
	type WorkflowResourceWorkflow,
} from "@/services/workflow-resources";

type View = "runs" | "workflows";
type Sort = "cpu" | "elapsed" | "memory" | "ai" | "started";
const PAGE_SIZE = 50;

function duration(seconds: number | null | undefined) {
	if (seconds == null) return "—";
	if (seconds < 1) return "<1s";
	if (seconds < 10) return `${seconds.toFixed(1)}s`;
	const wholeSeconds = Math.round(seconds);
	if (wholeSeconds < 60) return `${wholeSeconds}s`;
	const hours = Math.floor(wholeSeconds / 3600);
	const minutes = Math.floor((wholeSeconds % 3600) / 60);
	const remainingSeconds = wholeSeconds % 60;
	if (hours > 0) return `${hours}h ${minutes}m`;
	return `${minutes}m ${remainingSeconds}s`;
}

function bytes(value: number | null | undefined) {
	if (value == null) return "—";
	return `${(value / 1024 / 1024).toFixed(0)} MiB`;
}

function percentage(cores: number | null | undefined) {
	if (cores == null) return "—";
	return `${(cores * 100).toFixed(0)}%`;
}

function money(value: string | number | null | undefined) {
	const amount = Number(value ?? 0);
	if (amount > 0 && amount < 0.000001) return "<$0.000001";
	if (amount > 0 && amount < 0.01) {
		return `$${amount.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
	}
	return `$${amount.toFixed(2)}`;
}

function RunTable({ runs }: { runs: WorkflowResourceRun[] }) {
	const navigate = useNavigate();
	const location = useLocation();
	const usageReturn = `${location.pathname}${location.search}`;
	return (
		<DataTable className="min-w-0" aria-label="Workflow resource runs">
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead className="w-full min-w-52">
						Workflow
					</DataTableHead>
					<DataTableHead className="w-px">Status</DataTableHead>
					<DataTableHead className="w-px whitespace-nowrap">
						Started
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Duration
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						CPU Time
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Average CPU %
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Peak CPU %
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Peak Memory
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						AI Usage
					</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{runs.map((run) => (
					<DataTableRow
						key={run.execution_id}
						clickable
						href={`/history/${run.execution_id}`}
						onClick={() =>
							navigate(`/history/${run.execution_id}`, {
								state: { usageReturn },
							})
						}
					>
						<DataTableCell className="max-w-0">
							<Link
								to={`/history/${run.execution_id}`}
								state={{ usageReturn }}
								className="block truncate rounded-[var(--bf-radius-control)] font-mono font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							>
								{run.workflow_name}
							</Link>
							<div className="truncate text-xs text-muted-foreground">
								{run.organization_name ?? "Global"}
							</div>
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap">
							<RunStatusBadge status={run.status} />
						</DataTableCell>
						<DataTableCell
							className="whitespace-nowrap text-sm tabular-nums text-muted-foreground"
							title={
								run.started_at
									? formatDate(new Date(run.started_at))
									: undefined
							}
						>
							{run.started_at
								? formatRunTime(run.started_at)
								: "—"}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{duration(
								run.duration_ms == null
									? null
									: run.duration_ms / 1000,
							)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{duration(run.cpu_total_seconds)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{percentage(run.avg_cpu_cores)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{percentage(run.peak_cpu_cores)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{bytes(run.peak_process_rss_bytes)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							<div>{money(run.ai_cost)}</div>
							<div className="text-xs text-muted-foreground">
								{run.ai_calls} calls · {run.ai_tokens} tokens
							</div>
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}

function WorkflowTable({
	workflows,
	onSelect,
}: {
	workflows: WorkflowResourceWorkflow[];
	onSelect: (workflow: WorkflowResourceWorkflow) => void;
}) {
	return (
		<DataTable className="min-w-0" aria-label="Workflow resource ranking">
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead className="w-full min-w-52">
						Workflow
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Runs
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Failed
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						CPU Time
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Elapsed Time
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Peak CPU %
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						Peak Memory
					</DataTableHead>
					<DataTableHead className="w-px text-right">
						AI Spend
					</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{workflows.map((workflow) => (
					<DataTableRow
						key={`${workflow.workflow_id ?? "legacy"}:${workflow.workflow_name}`}
						clickable
						onClick={() => onSelect(workflow)}
					>
						<DataTableCell className="max-w-0">
							<Button
								variant="link"
								className="h-auto max-w-full justify-start truncate p-0 font-mono font-medium"
								onClick={() => onSelect(workflow)}
							>
								{workflow.workflow_name}
							</Button>
							{workflow.workflow_id && (
								<div className="text-xs text-muted-foreground">
									ID {workflow.workflow_id.slice(0, 8)}
								</div>
							)}
						</DataTableCell>
						<DataTableCell className="text-right tabular-nums">
							{workflow.run_count}
						</DataTableCell>
						<DataTableCell className="text-right tabular-nums">
							{workflow.failed_count}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{duration(workflow.total_cpu_seconds)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{duration(workflow.total_duration_ms / 1000)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{percentage(workflow.max_peak_cpu_cores)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{bytes(workflow.max_peak_process_rss_bytes)}
						</DataTableCell>
						<DataTableCell className="whitespace-nowrap text-right tabular-nums">
							{money(workflow.total_ai_cost)}
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}

interface WorkflowResourcesReportProps {
	reportSwitch: ReactNode;
}

export function WorkflowResourcesReport({
	reportSwitch,
}: WorkflowResourcesReportProps) {
	const [searchParams, setSearchParams] = useSearchParams();
	useEffect(() => {
		if (
			searchParams.has("workflow_from") ||
			searchParams.has("workflow_to")
		)
			return;
		const today = new Date();
		setSearchParams(
			(previous) => {
				const next = new URLSearchParams(previous);
				if (next.has("workflow_from") || next.has("workflow_to"))
					return next;
				next.set(
					"workflow_from",
					format(subDays(today, 7), "yyyy-MM-dd"),
				);
				next.set("workflow_to", format(today, "yyyy-MM-dd"));
				return next;
			},
			{ replace: true },
		);
	}, [searchParams, setSearchParams]);
	const updateParams = (updates: Record<string, string | null>) => {
		setSearchParams(
			(previous) => {
				const next = new URLSearchParams(previous);
				for (const [key, value] of Object.entries(updates)) {
					if (value === null) next.delete(key);
					else next.set(key, value);
				}
				return next;
			},
			{ replace: true },
		);
	};
	const changeFilter = (updates: Record<string, string | null>) =>
		updateParams({ ...updates, workflow_page: null });
	const readDate = (key: string) => {
		const raw = searchParams.get(key);
		if (!raw) return undefined;
		const value = parse(raw, "yyyy-MM-dd", new Date());
		return isValid(value) && format(value, "yyyy-MM-dd") === raw
			? value
			: undefined;
	};
	const view: View =
		searchParams.get("workflow_tab") === "workflows" ? "workflows" : "runs";
	const selectedFrom = readDate("workflow_from");
	const dateRange: DateRange = selectedFrom
		? { from: selectedFrom, to: readDate("workflow_to") }
		: { from: subDays(new Date(), 7), to: new Date() };
	const orgId = searchParams.get("workflow_org") ?? undefined;
	const workflowSearch = searchParams.get("workflow_search") ?? "";
	const workflowId = searchParams.get("workflow_id") ?? undefined;
	const statusParam = searchParams.get("workflow_status");
	const status: WorkflowResourceStatus | "" =
		statusParam === "Scheduled" ||
		statusParam === "Pending" ||
		statusParam === "Running" ||
		statusParam === "Success" ||
		statusParam === "CompletedWithErrors" ||
		statusParam === "Failed" ||
		statusParam === "Timeout" ||
		statusParam === "Stuck" ||
		statusParam === "Cancelling" ||
		statusParam === "Cancelled"
			? statusParam
			: "";
	const sortParam = searchParams.get("workflow_sort");
	const sort: Sort =
		sortParam === "cpu" ||
		sortParam === "elapsed" ||
		sortParam === "memory" ||
		sortParam === "ai" ||
		sortParam === "started"
			? sortParam
			: view === "workflows"
				? "cpu"
				: "started";
	const pageParam = Number(searchParams.get("workflow_page"));
	const page =
		Number.isSafeInteger(pageParam) && pageParam > 0 ? pageParam : 1;
	const start = startOfDay(dateRange.from ?? subDays(new Date(), 7));
	const selectedEnd = dateRange.to ?? dateRange.from ?? new Date();
	const end = endOfDay(selectedEnd);
	const { data, isLoading, error, refetch, isFetching } =
		useWorkflowResourceReport({
			startedAfter: start.toISOString(),
			startedBefore: end.toISOString(),
			view,
			sort,
			page,
			pageSize: PAGE_SIZE,
			orgId: typeof orgId === "string" ? orgId : undefined,
			workflowId,
			workflow: workflowId
				? undefined
				: workflowSearch.trim() || undefined,
			status: status || undefined,
		});
	const summary = data?.summary;
	const selectWorkflow = (selected: WorkflowResourceWorkflow) => {
		updateParams({
			workflow_id: selected.workflow_id ?? null,
			workflow_search: selected.workflow_name,
			workflow_tab: null,
			workflow_sort: null,
			workflow_page: null,
		});
	};

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1440px] min-w-0 lg:h-auto lg:flex-1">
			<ListPageHeader
				title="Usage"
				titleAccessory={reportSwitch}
				description="Find workflows putting the most pressure on workers and inspect their runs."
			/>
			<PageScrollArea className="space-y-5">
				<ListToolbar className="items-stretch">
					<div className="flex w-full min-w-0 flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
						<SearchBox
							value={workflowSearch}
							onChange={(value) => {
								changeFilter({
									workflow_search: value || null,
									workflow_id: null,
								});
							}}
							placeholder="Search workflows…"
							aria-label="Search workflows"
							className="w-full min-w-0 lg:min-w-52 lg:flex-1"
						/>
						<div className="w-full min-w-0 lg:w-44">
							<OrganizationSelect
								value={orgId}
								onChange={(value) => {
									changeFilter({
										workflow_org:
											typeof value === "string"
												? value
												: null,
									});
								}}
								showAll
								placeholder="All organizations"
							/>
						</div>
						<Select
							value={status || "all"}
							onValueChange={(value) => {
								changeFilter({
									workflow_status:
										value === "all" ? null : value,
								});
							}}
						>
							<SelectTrigger
								aria-label="Status"
								className="w-full lg:w-44"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="all">
									All statuses
								</SelectItem>
								<SelectItem value="Scheduled">
									Scheduled
								</SelectItem>
								<SelectItem value="Pending">Pending</SelectItem>
								<SelectItem value="Running">Running</SelectItem>
								<SelectItem value="Success">
									Completed
								</SelectItem>
								<SelectItem value="CompletedWithErrors">
									Completed with errors
								</SelectItem>
								<SelectItem value="Failed">Failed</SelectItem>
								<SelectItem value="Timeout">
									Timed out
								</SelectItem>
								<SelectItem value="Stuck">Stuck</SelectItem>
								<SelectItem value="Cancelling">
									Cancelling
								</SelectItem>
								<SelectItem value="Cancelled">
									Cancelled
								</SelectItem>
							</SelectContent>
						</Select>
						<DateRangePicker
							dateRange={dateRange}
							onDateRangeChange={(value) => {
								changeFilter({
									workflow_from: value?.from
										? format(value.from, "yyyy-MM-dd")
										: null,
									workflow_to: value?.to
										? format(value.to, "yyyy-MM-dd")
										: null,
								});
							}}
							maxDays={29}
							className="w-full min-w-0 sm:w-auto lg:w-80"
						/>
					</div>
				</ListToolbar>
				{error && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							{data
								? "Workflow resource data could not be refreshed. Showing the last loaded report."
								: "Workflow resource data could not be loaded."}{" "}
							<Button
								variant="outline"
								disabled={isFetching}
								onClick={() => void refetch()}
							>
								Retry report
							</Button>
						</AlertDescription>
					</Alert>
				)}
				<div
					className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
					aria-label="Workflow resource summary"
				>
					{[
						["Runs", summary?.run_count ?? "—"],
						["CPU Time", duration(summary?.total_cpu_seconds)],
						[
							"Elapsed Time",
							duration(
								summary?.total_duration_ms == null
									? null
									: summary.total_duration_ms / 1000,
							),
						],
						[
							"AI Spend",
							summary ? money(summary.total_ai_cost) : "—",
						],
					].map(([label, value]) => (
						<Card key={label}>
							<CardContent className="pt-5">
								<div className="text-sm text-muted-foreground">
									{label}
								</div>
								<div className="mt-2 text-2xl font-semibold">
									{value}
								</div>
							</CardContent>
						</Card>
					))}
				</div>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<Tabs
						value={view}
						onValueChange={(value) => {
							if (value === "workflows" && workflowId) {
								updateParams({
									workflow_id: null,
									workflow_search: null,
									workflow_tab: "workflows",
									workflow_sort: null,
									workflow_page: null,
								});
								return;
							}
							updateParams({
								workflow_tab:
									value === "workflows" ? "workflows" : null,
								workflow_sort: null,
								workflow_page: null,
							});
						}}
					>
						<TabsList aria-label="Workflow resource view">
							<TabsTrigger value="runs">Runs</TabsTrigger>
							<TabsTrigger value="workflows">
								By Workflow
							</TabsTrigger>
						</TabsList>
					</Tabs>
					<Select
						value={sort}
						onValueChange={(value) => {
							changeFilter({ workflow_sort: value });
						}}
					>
						<SelectTrigger aria-label="Sort by" className="w-44">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="started">Most Recent</SelectItem>
							<SelectItem value="cpu">CPU Time</SelectItem>
							<SelectItem value="elapsed">
								Elapsed Time
							</SelectItem>
							<SelectItem value="memory">Peak Memory</SelectItem>
							<SelectItem value="ai">AI Spend</SelectItem>
						</SelectContent>
					</Select>
				</div>
				{isLoading ? (
					<p role="status">Loading workflow resources…</p>
				) : view === "runs" ? (
					<RunTable runs={data?.runs ?? []} />
				) : (
					<WorkflowTable
						workflows={data?.workflows ?? []}
						onSelect={selectWorkflow}
					/>
				)}
				{!isLoading &&
					(!error || data) &&
					(view === "runs"
						? !data?.runs?.length
						: !data?.workflows?.length) && (
						<p className="py-4 text-center text-sm text-muted-foreground">
							No workflow runs match these filters.
						</p>
					)}
				{(page > 1 || (data?.total ?? 0) > PAGE_SIZE) && (
					<PaginationFooter
						aria-label="Workflow resource pages"
						summary={`${data?.total ?? 0} ${view === "runs" ? "runs" : "workflows"} · Page ${page}`}
						pending={isFetching}
						previousDisabled={page === 1 || isFetching}
						nextDisabled={
							!data ||
							page * PAGE_SIZE >= data.total ||
							isFetching
						}
						onPrevious={() =>
							updateParams({
								workflow_page:
									page > 2 ? String(page - 1) : null,
							})
						}
						onNext={() =>
							updateParams({ workflow_page: String(page + 1) })
						}
					/>
				)}
				<p className="text-xs text-muted-foreground">
					Average CPU is CPU time divided by elapsed time. Peak CPU is
					the busiest sample, checked about once a second; short
					spikes can be missed. 100% means one core fully occupied.
					Peak Memory is the workflow process high-water mark for
					completed runs. Historical runs have no peak values.
				</p>
			</PageScrollArea>
		</PageWorkspace>
	);
}
