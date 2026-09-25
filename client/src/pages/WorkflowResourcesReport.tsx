import { useState } from "react";
import { Link } from "react-router-dom";
import { format, subDays } from "date-fns";
import { AlertCircle } from "lucide-react";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

function money(value: string | number | null | undefined) {
	const amount = Number(value ?? 0);
	if (amount > 0 && amount < 0.000001) return "<$0.000001";
	if (amount > 0 && amount < 0.01) {
		return `$${amount.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
	}
	return `$${amount.toFixed(2)}`;
}

function cpuAverage(run: WorkflowResourceRun) {
	if (run.avg_cpu_cores == null) return "—";
	return `${(run.avg_cpu_cores * 100).toFixed(0)}% of one core`;
}

function RunTable({ runs }: { runs: WorkflowResourceRun[] }) {
	return (
		<div className="overflow-x-auto">
			<DataTable className="min-w-[1040px]">
				<DataTableHeader>
					<DataTableRow>
						<DataTableHead>Started</DataTableHead>
						<DataTableHead>Workflow</DataTableHead>
						<DataTableHead>Status</DataTableHead>
						<DataTableHead>Elapsed</DataTableHead>
						<DataTableHead>CPU time</DataTableHead>
						<DataTableHead>Average CPU</DataTableHead>
						<DataTableHead>Peak sampled CPU</DataTableHead>
						<DataTableHead>Peak process memory</DataTableHead>
						<DataTableHead>AI usage</DataTableHead>
						<DataTableHead>
							<span className="sr-only">Execution</span>
						</DataTableHead>
					</DataTableRow>
				</DataTableHeader>
				<DataTableBody>
					{runs.map((run) => (
						<DataTableRow key={run.execution_id}>
							<DataTableCell className="whitespace-nowrap">
								{run.started_at
									? format(
											new Date(run.started_at),
											"MMM d, HH:mm",
										)
									: "—"}
							</DataTableCell>
							<DataTableCell>
								<div className="font-medium">
									{run.workflow_name}
								</div>
								<div className="text-xs text-muted-foreground">
									{run.organization_name ?? "Global"}
								</div>
							</DataTableCell>
							<DataTableCell>{run.status}</DataTableCell>
							<DataTableCell>
								{duration(
									run.duration_ms == null
										? null
										: run.duration_ms / 1000,
								)}
							</DataTableCell>
							<DataTableCell>
								{duration(run.cpu_total_seconds)}
							</DataTableCell>
							<DataTableCell>{cpuAverage(run)}</DataTableCell>
							<DataTableCell>
								{run.peak_cpu_cores == null
									? "—"
									: `${(run.peak_cpu_cores * 100).toFixed(0)}% of one core`}
							</DataTableCell>
							<DataTableCell>
								{bytes(run.peak_process_rss_bytes)}
							</DataTableCell>
							<DataTableCell>
								{money(run.ai_cost)} · {run.ai_calls} calls ·{" "}
								{run.ai_tokens} tokens
							</DataTableCell>
							<DataTableCell>
								<Link
									className="text-primary hover:underline"
									to={`/history/${run.execution_id}`}
								>
									View run
								</Link>
							</DataTableCell>
						</DataTableRow>
					))}
				</DataTableBody>
			</DataTable>
		</div>
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
		<div className="overflow-x-auto">
			<DataTable className="min-w-[850px]">
				<DataTableHeader>
					<DataTableRow>
						<DataTableHead>Workflow</DataTableHead>
						<DataTableHead>Runs</DataTableHead>
						<DataTableHead>Failed</DataTableHead>
						<DataTableHead>Total CPU</DataTableHead>
						<DataTableHead>Total elapsed</DataTableHead>
						<DataTableHead>Highest peak memory</DataTableHead>
						<DataTableHead>AI spend</DataTableHead>
					</DataTableRow>
				</DataTableHeader>
				<DataTableBody>
					{workflows.map((workflow) => (
						<DataTableRow
							key={`${workflow.workflow_id ?? "legacy"}:${workflow.workflow_name}`}
						>
							<DataTableCell>
								<Button
									variant="link"
									className="h-auto p-0"
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
							<DataTableCell>{workflow.run_count}</DataTableCell>
							<DataTableCell>
								{workflow.failed_count}
							</DataTableCell>
							<DataTableCell>
								{duration(workflow.total_cpu_seconds)}
							</DataTableCell>
							<DataTableCell>
								{duration(workflow.total_duration_ms / 1000)}
							</DataTableCell>
							<DataTableCell>
								{bytes(workflow.max_peak_process_rss_bytes)}
							</DataTableCell>
							<DataTableCell>
								{money(workflow.total_ai_cost)}
							</DataTableCell>
						</DataTableRow>
					))}
				</DataTableBody>
			</DataTable>
		</div>
	);
}

export function WorkflowResourcesReport() {
	const [view, setView] = useState<View>("runs");
	const [range, setRange] = useState(1);
	const [rangeEnd] = useState(() => new Date());
	const [orgId, setOrgId] = useState<string | null | undefined>();
	const [workflow, setWorkflow] = useState("");
	const [workflowId, setWorkflowId] = useState<string>();
	const [draftWorkflow, setDraftWorkflow] = useState("");
	const [status, setStatus] = useState<WorkflowResourceStatus | "">("");
	const [sort, setSort] = useState<Sort>("cpu");
	const [page, setPage] = useState(1);
	const { data, isLoading, error, refetch, isFetching } =
		useWorkflowResourceReport({
			startedAfter: subDays(rangeEnd, range).toISOString(),
			startedBefore: rangeEnd.toISOString(),
			view,
			sort,
			page,
			pageSize: 50,
			orgId: typeof orgId === "string" ? orgId : undefined,
			workflowId,
			workflow: workflow.trim() || undefined,
			status: status || undefined,
		});
	const summary = data?.summary;
	const selectWorkflow = (selected: WorkflowResourceWorkflow) => {
		setWorkflowId(selected.workflow_id ?? undefined);
		setWorkflow(selected.workflow_id ? "" : selected.workflow_name);
		setDraftWorkflow(selected.workflow_name);
		setView("runs");
		setPage(1);
	};
	const changeFilter = () => setPage(1);

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1440px] min-w-0 lg:h-auto lg:flex-1">
			<ListPageHeader
				title="Workflow resources"
				description="Find expensive workflows and inspect individual runs."
			/>
			<PageScrollArea className="space-y-5">
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
						["CPU time", duration(summary?.total_cpu_seconds)],
						[
							"Wall time",
							duration(
								summary?.total_duration_ms == null
									? null
									: summary.total_duration_ms / 1000,
							),
						],
						[
							"AI spend",
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
				<Card className="overflow-hidden">
					<CardContent className="space-y-4 pt-5">
						<div className="flex flex-wrap items-center gap-3">
							<label className="text-sm">
								Period{" "}
								<select
									className="ml-2 rounded-md border bg-background px-3 py-2"
									value={range}
									onChange={(event) => {
										setRange(Number(event.target.value));
										changeFilter();
									}}
								>
									<option value={1}>Last 24 hours</option>
									<option value={7}>Last 7 days</option>
									<option value={30}>Last 30 days</option>
								</select>
							</label>
							<div className="min-w-48">
								<OrganizationSelect
									value={orgId}
									onChange={(value) => {
										setOrgId(value);
										changeFilter();
									}}
									showAll
									placeholder="All organizations"
								/>
							</div>
							<form
								className="flex gap-2"
								onSubmit={(event) => {
									event.preventDefault();
									setWorkflowId(undefined);
									setWorkflow(draftWorkflow);
									changeFilter();
								}}
							>
								<Input
									aria-label="Search workflows"
									placeholder="Search workflows"
									className="w-56"
									value={draftWorkflow}
									onChange={(event) =>
										setDraftWorkflow(event.target.value)
									}
								/>
								<Button type="submit" variant="outline">
									Search
								</Button>
							</form>
							<label className="text-sm">
								Status{" "}
								<select
									className="ml-2 rounded-md border bg-background px-3 py-2"
									value={status}
									onChange={(event) => {
										setStatus(
											event.target.value as
												WorkflowResourceStatus | "",
										);
										changeFilter();
									}}
								>
									<option value="">All</option>
									<option value="Scheduled">Scheduled</option>
									<option value="Pending">Pending</option>
									<option value="Running">Running</option>
									<option value="Success">Success</option>
									<option value="CompletedWithErrors">
										Completed with errors
									</option>
									<option value="Failed">Failed</option>
									<option value="Timeout">Timed out</option>
									<option value="Stuck">Stuck</option>
									<option value="Cancelling">
										Cancelling
									</option>
									<option value="Cancelled">Cancelled</option>
								</select>
							</label>
						</div>
						<div className="flex flex-wrap items-center justify-between gap-3">
							<Tabs
								value={view}
								onValueChange={(value) => {
									if (value === "workflows" && workflowId) {
										setWorkflowId(undefined);
										setWorkflow("");
										setDraftWorkflow("");
									}
									setView(value as View);
									setPage(1);
								}}
							>
								<TabsList>
									<TabsTrigger value="runs">Runs</TabsTrigger>
									<TabsTrigger value="workflows">
										By workflow
									</TabsTrigger>
								</TabsList>
							</Tabs>
							<label className="text-sm">
								Sort{" "}
								<select
									className="ml-2 rounded-md border bg-background px-3 py-2"
									value={sort}
									onChange={(event) => {
										setSort(event.target.value as Sort);
										setPage(1);
									}}
								>
									<option value="cpu">CPU time</option>
									<option value="elapsed">
										Elapsed time
									</option>
									<option value="memory">Memory</option>
									<option value="ai">AI spend</option>
									<option value="started">Most recent</option>
								</select>
							</label>
						</div>
						<p className="text-xs text-muted-foreground">
							Average CPU is CPU time divided by elapsed time.
							Peak CPU is the busiest sample, checked about once a
							second and at completion; shorter spikes can be
							missed. 100% means one core fully occupied; a run
							can exceed 100% when it uses multiple cores. Peak
							process memory is the child's highest RSS for
							completed runs; interrupted runs use the highest
							available sample. Historical runs have no value.
						</p>
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
						<div className="flex items-center justify-between text-sm text-muted-foreground">
							<span>
								Page {page} · {data?.total ?? 0}{" "}
								{view === "runs" ? "runs" : "workflows"}
							</span>
							<div className="flex gap-2">
								<Button
									variant="outline"
									disabled={page === 1}
									onClick={() => setPage(page - 1)}
								>
									Previous
								</Button>
								<Button
									variant="outline"
									disabled={!data || page * 50 >= data.total}
									onClick={() => setPage(page + 1)}
								>
									Next
								</Button>
							</div>
						</div>
					</CardContent>
				</Card>
			</PageScrollArea>
		</PageWorkspace>
	);
}
