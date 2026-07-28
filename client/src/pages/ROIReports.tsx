import { useState, useMemo } from "react";
import { format, subDays } from "date-fns";
import type { DateRange } from "react-day-picker";
import {
	DollarSign,
	Clock,
	TrendingUp,
	Download,
	AlertCircle,
	Sparkles,
	ChevronUp,
	ChevronDown,
} from "lucide-react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	LineChart,
	Line,
	XAxis,
	YAxis,
	CartesianGrid,
	Tooltip,
	ResponsiveContainer,
	Legend,
} from "recharts";
import {
	useROISummary,
	useROIByWorkflow,
	useROIByOrganization,
	useROITrends,
	type ROISummary,
	type ROIByWorkflow,
	type ROIByOrganization,
	type ROITrends,
	type WorkflowROI,
} from "@/services/reports";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { formatChartDateLabel } from "@/components/reports/formatters";

// ============================================================================
// Demo Data Generation
// ============================================================================

// Extended type to include organization_id for demo filtering
type DemoWorkflowROI = WorkflowROI & { organization_id: string };

// Demo workflow templates (workflow names and base metrics)
const DEMO_WORKFLOW_TEMPLATES = [
	{ name: "User Onboarding", timeSaved: 45, value: 75, baseCount: 2800 },
	{ name: "Ticket Triage", timeSaved: 15, value: 25, baseCount: 4500 },
	{ name: "Invoice Processing", timeSaved: 30, value: 50, baseCount: 1900 },
	{ name: "Compliance Check", timeSaved: 60, value: 120, baseCount: 750 },
	{
		name: "Data Backup Verification",
		timeSaved: 20,
		value: 35,
		baseCount: 2100,
	},
	{ name: "Report Generation", timeSaved: 25, value: 40, baseCount: 700 },
];

// Fallback demo orgs if no real orgs exist
const FALLBACK_DEMO_ORGS = [
	{ id: "demo-org-1", name: "Acme Corp" },
	{ id: "demo-org-2", name: "TechStart Inc" },
	{ id: "demo-org-3", name: "Global Services LLC" },
];

interface DemoDataParams {
	startDate: string;
	endDate: string;
	orgId: string | null; // null = global scope
	realOrgs: Array<{ id: string; name: string }> | undefined;
}

interface DemoDataResult {
	summary: ROISummary;
	byWorkflow: ROIByWorkflow;
	byOrg: ROIByOrganization;
	trends: ROITrends;
}

/**
 * Generate demo data that mirrors API response structure.
 * Accepts orgId and returns pre-filtered data - exactly like the API would.
 *
 * This ensures demo data and real API data work identically:
 * - Pass in org filter (orgId)
 * - Get back filtered response
 * - Same downstream code for both paths
 */
function generateDemoData(params: DemoDataParams): DemoDataResult {
	const { startDate, endDate, orgId, realOrgs } = params;
	const orgs =
		realOrgs && realOrgs.length > 0 ? realOrgs : FALLBACK_DEMO_ORGS;

	// Step 1: Generate all workflows with org assignments
	const allWorkflows: DemoWorkflowROI[] = DEMO_WORKFLOW_TEMPLATES.map(
		(template, index) => {
			const org = orgs[index % orgs.length];
			const variance = 0.9 + Math.random() * 0.2;
			const executions = Math.floor(template.baseCount * variance);
			const successRate = 0.96 + Math.random() * 0.03;
			const successCount = Math.floor(executions * successRate);

			return {
				workflow_id: `demo-${index + 1}`,
				workflow_name: template.name,
				organization_id: org.id,
				execution_count: executions,
				success_count: successCount,
				time_saved_per_execution: template.timeSaved,
				value_per_execution: template.value,
				total_time_saved: successCount * template.timeSaved,
				total_value: successCount * template.value,
			};
		},
	);

	// Step 2: Filter to selected org (like the API would)
	const filteredWorkflows = orgId
		? allWorkflows.filter((w) => w.organization_id === orgId)
		: allWorkflows;

	// Step 3: Compute summary from filtered workflows
	const summaryTotals = filteredWorkflows.reduce(
		(acc, w) => ({
			total_executions: acc.total_executions + w.execution_count,
			successful_executions: acc.successful_executions + w.success_count,
			total_time_saved: acc.total_time_saved + w.total_time_saved,
			total_value: acc.total_value + w.total_value,
		}),
		{
			total_executions: 0,
			successful_executions: 0,
			total_time_saved: 0,
			total_value: 0,
		},
	);

	const summary: ROISummary = {
		start_date: startDate,
		end_date: endDate,
		...summaryTotals,
		time_saved_unit: "minutes",
		value_unit: "USD",
	};

	// Step 4: Build byWorkflow response (already filtered)
	const byWorkflow: ROIByWorkflow = {
		workflows: filteredWorkflows,
		total_workflows: filteredWorkflows.length,
		time_saved_unit: "minutes",
		value_unit: "USD",
	};

	// Step 5: Build byOrg response (aggregated from workflows)
	// Group workflows by organization and aggregate
	const orgMetrics = new Map<
		string,
		{
			name: string;
			execution_count: number;
			success_count: number;
			total_time_saved: number;
			total_value: number;
		}
	>();

	// Use allWorkflows for org breakdown (API always returns all orgs)
	for (const workflow of allWorkflows) {
		const existing = orgMetrics.get(workflow.organization_id);
		const orgName =
			orgs.find((o) => o.id === workflow.organization_id)?.name ??
			"Unknown";

		if (existing) {
			existing.execution_count += workflow.execution_count;
			existing.success_count += workflow.success_count;
			existing.total_time_saved += workflow.total_time_saved;
			existing.total_value += workflow.total_value;
		} else {
			orgMetrics.set(workflow.organization_id, {
				name: orgName,
				execution_count: workflow.execution_count,
				success_count: workflow.success_count,
				total_time_saved: workflow.total_time_saved,
				total_value: workflow.total_value,
			});
		}
	}

	const byOrg: ROIByOrganization = {
		organizations: Array.from(orgMetrics.entries()).map(
			([id, metrics]) => ({
				organization_id: id,
				organization_name: metrics.name,
				execution_count: metrics.execution_count,
				success_count: metrics.success_count,
				total_time_saved: metrics.total_time_saved,
				total_value: metrics.total_value,
			}),
		),
		time_saved_unit: "minutes",
		value_unit: "USD",
	};

	// Step 6: Generate trends based on filtered workflows
	// Distribute workflow metrics across days in the date range
	const trendEntries: ROITrends["entries"] = [];
	const start = new Date(startDate);
	const end = new Date(endDate);
	const dayCount = Math.max(
		1,
		Math.ceil((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)) +
			1,
	);

	// Calculate daily average from filtered workflows
	const dailyAvgExecutions = summaryTotals.total_executions / dayCount;
	const dailyAvgSuccess = summaryTotals.successful_executions / dayCount;
	const dailyAvgTimeSaved = summaryTotals.total_time_saved / dayCount;
	const dailyAvgValue = summaryTotals.total_value / dayCount;

	const currentDate = new Date(start);
	let dayIndex = 0;

	while (currentDate <= end) {
		const isWeekend =
			currentDate.getDay() === 0 || currentDate.getDay() === 6;
		const baseMultiplier = isWeekend ? 0.5 : 1;
		const trendMultiplier = 1 + dayIndex * 0.003;
		const variance = 0.85 + Math.random() * 0.3;
		const dayFactor = baseMultiplier * trendMultiplier * variance;

		trendEntries.push({
			period: format(currentDate, "yyyy-MM-dd"),
			execution_count: Math.round(dailyAvgExecutions * dayFactor),
			success_count: Math.round(dailyAvgSuccess * dayFactor),
			time_saved: Math.round(dailyAvgTimeSaved * dayFactor),
			value: Math.round(dailyAvgValue * dayFactor * 100) / 100,
		});

		currentDate.setDate(currentDate.getDate() + 1);
		dayIndex++;
	}

	const trends: ROITrends = {
		entries: trendEntries,
		granularity: "day",
		time_saved_unit: "minutes",
		value_unit: "USD",
	};

	return { summary, byWorkflow, byOrg, trends };
}

// ============================================================================
// Component
// ============================================================================

// Sort state type
type SortConfig = { by: string; dir: "asc" | "desc" };

export function ROIReports() {
	const { isPlatformAdmin } = useAuth();

	// Organization filter state (platform admins only)
	// undefined = all, null = global only, UUID string = specific org
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);

	// Derive isGlobalScope from filterOrgId for display logic
	const isGlobalScope = filterOrgId === undefined || filterOrgId === null;

	// Fetch real organizations for demo data generation
	const { data: orgsData } = useOrganizations({ enabled: isPlatformAdmin });

	// Demo mode state
	const [showDemoData, setShowDemoData] = useState(false);

	// Default to last 30 days
	const [dateRange, setDateRange] = useState<DateRange | undefined>({
		from: subDays(new Date(), 30),
		to: new Date(),
	});

	// Sorting state for tables
	const [workflowSort, setWorkflowSort] = useState<SortConfig>({
		by: "value",
		dir: "desc",
	});
	const [orgSort, setOrgSort] = useState<SortConfig>({
		by: "value",
		dir: "desc",
	});

	// Format dates for API (YYYY-MM-DD)
	const startDate = dateRange?.from
		? format(dateRange.from, "yyyy-MM-dd")
		: "";
	const endDate = dateRange?.to ? format(dateRange.to, "yyyy-MM-dd") : "";

	// Memoize orgs array for stable reference
	// The API returns an array directly, not { organizations: [...] }
	const realOrgs = useMemo(() => {
		if (!orgsData || !Array.isArray(orgsData)) return undefined;
		return orgsData.map((o) => ({ id: o.id, name: o.name }));
	}, [orgsData]);

	// Single source of demo data - already filtered by org like API would be
	// Use filterOrgId for filtering (undefined/null means show all orgs)
	const demoData = useMemo(() => {
		if (!startDate || !endDate) return null;
		return generateDemoData({
			startDate,
			endDate,
			orgId: filterOrgId ?? null, // Apply org filter here, like API does
			realOrgs,
		});
	}, [startDate, endDate, filterOrgId, realOrgs]);

	// Fetch real data (only used when not in demo mode)
	const {
		data: realSummary,
		isLoading: summaryLoading,
		error: summaryError,
	} = useROISummary(startDate, endDate, filterOrgId);

	const {
		data: realByWorkflow,
		isLoading: workflowLoading,
		error: workflowError,
	} = useROIByWorkflow(startDate, endDate, filterOrgId);

	const {
		data: realByOrg,
		isLoading: orgLoading,
		error: orgError,
	} = useROIByOrganization(startDate, endDate);

	const {
		data: realTrends,
		isLoading: trendsLoading,
		error: trendsError,
	} = useROITrends(startDate, endDate, "day", filterOrgId);

	// Use demo or real data based on toggle
	// Both have the same shape and filtering already applied - no additional processing needed
	const summary = showDemoData ? demoData?.summary : realSummary;
	const byWorkflow = showDemoData ? demoData?.byWorkflow : realByWorkflow;
	const byOrg = showDemoData ? demoData?.byOrg : realByOrg;
	const trends = showDemoData ? demoData?.trends : realTrends;

	// Sorted workflows
	const sortedWorkflows = useMemo(() => {
		const workflows = byWorkflow?.workflows;
		if (!workflows) return [];
		return [...workflows].sort((a, b) => {
			const mult = workflowSort.dir === "desc" ? -1 : 1;
			switch (workflowSort.by) {
				case "name":
					return (
						mult * a.workflow_name.localeCompare(b.workflow_name)
					);
				case "executions":
					return mult * (a.execution_count - b.execution_count);
				case "time":
					return mult * (a.total_time_saved - b.total_time_saved);
				case "value":
					return mult * (a.total_value - b.total_value);
				default:
					return 0;
			}
		});
	}, [byWorkflow, workflowSort]);

	// Sorted organizations
	const sortedOrganizations = useMemo(() => {
		const organizations = byOrg?.organizations;
		if (!organizations) return [];
		return [...organizations].sort((a, b) => {
			const mult = orgSort.dir === "desc" ? -1 : 1;
			switch (orgSort.by) {
				case "name":
					return (
						mult *
						a.organization_name.localeCompare(b.organization_name)
					);
				case "executions":
					return mult * (a.execution_count - b.execution_count);
				case "time":
					return mult * (a.total_time_saved - b.total_time_saved);
				case "value":
					return mult * (a.total_value - b.total_value);
				default:
					return 0;
			}
		});
	}, [byOrg, orgSort]);

	// Sort toggle helpers
	const toggleWorkflowSort = (column: string) => {
		setWorkflowSort((prev) => ({
			by: column,
			dir: prev.by === column && prev.dir === "desc" ? "asc" : "desc",
		}));
	};

	const toggleOrgSort = (column: string) => {
		setOrgSort((prev) => ({
			by: column,
			dir: prev.by === column && prev.dir === "desc" ? "asc" : "desc",
		}));
	};

	// Loading states (instant when using demo data)
	const isLoading = showDemoData
		? false
		: summaryLoading || workflowLoading || orgLoading || trendsLoading;

	// Handle errors (no errors in demo mode)
	const hasError = showDemoData
		? false
		: !!(summaryError || workflowError || orgError || trendsError);

	// CSV Export handlers
	const downloadWorkflowCSV = () => {
		if (!byWorkflow?.workflows) return;

		const headers = [
			"Workflow Name",
			"Executions",
			"Total Time Saved (hrs)",
			"Total Value",
		];
		const rows = byWorkflow.workflows.map((w) => [
			w.workflow_name,
			w.execution_count,
			(w.total_time_saved / 60).toFixed(2),
			w.total_value.toFixed(2),
		]);

		const csv = [headers, ...rows].map((row) => row.join(",")).join("\n");
		const blob = new Blob([csv], { type: "text/csv" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `roi-by-workflow-${startDate}-${endDate}${showDemoData ? "-demo" : ""}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	};

	const downloadOrganizationCSV = () => {
		if (!byOrg?.organizations) return;

		const headers = [
			"Organization",
			"Executions",
			"Total Time Saved (hrs)",
			"Total Value",
		];
		const rows = byOrg.organizations.map((o) => [
			o.organization_name,
			o.execution_count,
			(o.total_time_saved / 60).toFixed(2),
			o.total_value.toFixed(2),
		]);

		const csv = [headers, ...rows].map((row) => row.join(",")).join("\n");
		const blob = new Blob([csv], { type: "text/csv" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `roi-by-organization-${startDate}-${endDate}${showDemoData ? "-demo" : ""}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	};

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="flex items-start justify-between">
				<div>
					<div className="flex items-center gap-3">
						<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
							ROI Reports
						</h1>
						{showDemoData && (
							<Badge
								variant="outline"
								className="bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800"
							>
								<Sparkles className="h-3 w-3 mr-1" />
								Demo Mode
							</Badge>
						)}
					</div>
					<p className="leading-7 mt-2 text-muted-foreground">
						Workflow automation value and time savings analytics
					</p>
				</div>

				{/* Demo Data Toggle - Only visible to platform admins */}
				{isPlatformAdmin && (
					<div className="flex items-center space-x-2 pt-2">
						<Switch
							id="demo-mode"
							checked={showDemoData}
							onCheckedChange={setShowDemoData}
						/>
						<Label
							htmlFor="demo-mode"
							className="text-sm text-muted-foreground cursor-pointer"
						>
							Show Demo Data
						</Label>
					</div>
				)}
			</div>

			{/* Demo Mode Banner */}
			{showDemoData && (
				<Alert className="bg-amber-50 border-amber-200 dark:bg-amber-950 dark:border-amber-800">
					<Sparkles className="h-4 w-4 text-amber-600 dark:text-amber-400" />
					<AlertDescription className="text-amber-800 dark:text-amber-200">
						Displaying sample data for demonstration purposes.
						Toggle off to view real ROI data.
					</AlertDescription>
				</Alert>
			)}

			{/* Date Range Picker and Organization Filter */}
			<Card>
				<CardHeader>
					<CardTitle>Report Period</CardTitle>
					<CardDescription>
						Select a date range for the ROI report
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="flex items-center gap-4">
						<DateRangePicker
							dateRange={dateRange}
							onDateRangeChange={setDateRange}
						/>
						{isPlatformAdmin && (
							<div className="w-64 ml-auto">
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
				</CardContent>
			</Card>

			{/* Error Alert */}
			{hasError && (
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						Failed to load ROI data. Please try again later.
					</AlertDescription>
				</Alert>
			)}

			{/* Summary Cards */}
			<div className="grid gap-4 md:grid-cols-3">
				{/* Total Executions */}
				<Card>
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Total Executions
						</CardTitle>
						<TrendingUp className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{isLoading || (!showDemoData && summaryLoading) ? (
							<Skeleton className="h-8 w-24" />
						) : (
							<div className="text-2xl font-bold">
								{(
									summary?.total_executions ?? 0
								).toLocaleString()}
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							Workflow runs in period
						</p>
					</CardContent>
				</Card>

				{/* Total Time Saved */}
				<Card>
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Total Time Saved
						</CardTitle>
						<Clock className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{isLoading || (!showDemoData && summaryLoading) ? (
							<Skeleton className="h-8 w-24" />
						) : (
							<div className="text-2xl font-bold">
								{summary
									? (summary.total_time_saved / 60).toFixed(1)
									: "0.0"}{" "}
								hrs
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							{summary?.time_saved_unit ?? "minutes"} saved by
							automation
						</p>
					</CardContent>
				</Card>

				{/* Total Value */}
				<Card>
					<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
						<CardTitle className="text-sm font-medium">
							Total Value
						</CardTitle>
						<DollarSign className="h-4 w-4 text-muted-foreground" />
					</CardHeader>
					<CardContent>
						{isLoading || (!showDemoData && summaryLoading) ? (
							<Skeleton className="h-8 w-24" />
						) : (
							<div className="text-2xl font-bold">
								{(summary?.total_value ?? 0).toLocaleString(
									"en-US",
									{
										style: "currency",
										currency: "USD",
									},
								)}
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							{summary?.value_unit ?? "USD"} value delivered
						</p>
					</CardContent>
				</Card>
			</div>

			{/* Trends Chart */}
			<Card>
				<CardHeader>
					<CardTitle>ROI Over Time</CardTitle>
					<CardDescription>
						Time savings and value trends during the selected period
					</CardDescription>
				</CardHeader>
				<CardContent>
					{isLoading || (!showDemoData && trendsLoading) ? (
						<Skeleton className="h-[300px] w-full" />
					) : trends?.entries && trends.entries.length > 0 ? (
						<ResponsiveContainer width="100%" height={300}>
							<LineChart
								data={trends.entries.map((entry) => ({
									...entry,
									time_saved_hours: entry.time_saved / 60,
								}))}
							>
								<CartesianGrid
									strokeDasharray="3 3"
									className="stroke-muted"
								/>
								<XAxis
									dataKey="period"
									className="text-xs"
									tick={{ fontSize: 12 }}
									tickFormatter={(value) =>
										format(new Date(value), "MMM dd")
									}
								/>
								<YAxis
									yAxisId="left"
									className="text-xs"
									tick={{ fontSize: 12 }}
									label={{
										value: "Hours Saved",
										angle: -90,
										position: "insideLeft",
										fontSize: 12,
									}}
								/>
								<YAxis
									yAxisId="right"
									orientation="right"
									className="text-xs"
									tick={{ fontSize: 12 }}
									label={{
										value: "Value",
										angle: 90,
										position: "insideRight",
										fontSize: 12,
									}}
								/>
								<Tooltip
									contentStyle={{
										backgroundColor: "hsl(var(--card))",
										border: "1px solid hsl(var(--border))",
										borderRadius: "6px",
									}}
									formatter={(value, name) => {
										const num = Number(value ?? 0);
										if (name === "time_saved_hours")
											return [
												`${num.toFixed(2)} hrs`,
												"Time Saved",
											];
										if (name === "value")
											return [
												`${num.toFixed(2)}`,
												"Value",
											];
										return [value as string | number, name as string];
									}}
									labelFormatter={formatChartDateLabel}
								/>
								<Legend
									formatter={(value) => {
										if (value === "time_saved_hours")
											return "Time Saved (hours)";
										if (value === "value")
											return `Value (${trends.value_unit})`;
										return value;
									}}
								/>
								<Line
									yAxisId="left"
									type="monotone"
									dataKey="time_saved_hours"
									stroke="hsl(var(--chart-1, 220 70% 50%))"
									strokeWidth={2}
									dot={{ r: 3 }}
									activeDot={{ r: 5 }}
								/>
								<Line
									yAxisId="right"
									type="monotone"
									dataKey="value"
									stroke="hsl(var(--chart-2, 160 60% 45%))"
									strokeWidth={2}
									dot={{ r: 3 }}
									activeDot={{ r: 5 }}
								/>
							</LineChart>
						</ResponsiveContainer>
					) : (
						<div className="flex items-center justify-center h-[300px] text-muted-foreground">
							No trend data available for this period
						</div>
					)}
				</CardContent>
			</Card>

			{/* Per-Workflow Table */}
			<Card>
				<CardHeader>
					<div className="flex items-center justify-between">
						<div>
							<CardTitle>ROI by Workflow</CardTitle>
							<CardDescription>
								Value delivered by each workflow
							</CardDescription>
						</div>
						<Button
							variant="outline"
							size="sm"
							onClick={downloadWorkflowCSV}
							disabled={
								!byWorkflow?.workflows ||
								byWorkflow.workflows.length === 0
							}
						>
							<Download className="h-4 w-4 mr-2" />
							Export CSV
						</Button>
					</div>
				</CardHeader>
				<CardContent>
					{isLoading || (!showDemoData && workflowLoading) ? (
						<div className="space-y-2">
							<Skeleton className="h-10 w-full" />
							<Skeleton className="h-10 w-full" />
							<Skeleton className="h-10 w-full" />
						</div>
					) : sortedWorkflows.length > 0 ? (
						<DataTable>
							<DataTableHeader>
								<DataTableRow>
									<DataTableHead
										className="cursor-pointer select-none transition-colors hover:bg-muted/50"
										onClick={() =>
											toggleWorkflowSort("name")
										}
									>
										<div className="flex items-center gap-1">
											Workflow
											{workflowSort.by === "name" &&
												(workflowSort.dir === "desc" ? (
													<ChevronDown className="h-4 w-4" />
												) : (
													<ChevronUp className="h-4 w-4" />
												))}
										</div>
									</DataTableHead>
									<DataTableHead
										className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
										onClick={() =>
											toggleWorkflowSort("executions")
										}
									>
										<div className="flex items-center justify-end gap-1">
											Executions
											{workflowSort.by === "executions" &&
												(workflowSort.dir === "desc" ? (
													<ChevronDown className="h-4 w-4" />
												) : (
													<ChevronUp className="h-4 w-4" />
												))}
										</div>
									</DataTableHead>
									<DataTableHead
										className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
										onClick={() =>
											toggleWorkflowSort("time")
										}
									>
										<div className="flex items-center justify-end gap-1">
											Time Saved (hrs)
											{workflowSort.by === "time" &&
												(workflowSort.dir === "desc" ? (
													<ChevronDown className="h-4 w-4" />
												) : (
													<ChevronUp className="h-4 w-4" />
												))}
										</div>
									</DataTableHead>
									<DataTableHead
										className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
										onClick={() =>
											toggleWorkflowSort("value")
										}
									>
										<div className="flex items-center justify-end gap-1">
											Value
											{workflowSort.by === "value" &&
												(workflowSort.dir === "desc" ? (
													<ChevronDown className="h-4 w-4" />
												) : (
													<ChevronUp className="h-4 w-4" />
												))}
										</div>
									</DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{sortedWorkflows.map((workflow) => (
									<DataTableRow key={workflow.workflow_id}>
										<DataTableCell className="font-medium">
											{workflow.workflow_name}
										</DataTableCell>
										<DataTableCell className="text-right">
											{workflow.execution_count.toLocaleString()}
										</DataTableCell>
										<DataTableCell className="text-right">
											{(
												workflow.total_time_saved / 60
											).toFixed(2)}
										</DataTableCell>
										<DataTableCell className="text-right">
											{workflow.total_value.toLocaleString(
												"en-US",
												{
													style: "currency",
													currency: "USD",
												},
											)}
										</DataTableCell>
									</DataTableRow>
								))}
							</DataTableBody>
						</DataTable>
					) : (
						<div className="flex items-center justify-center py-8 text-muted-foreground">
							No workflow data available for this period
						</div>
					)}
				</CardContent>
			</Card>

			{/* Per-Organization Table - Only shown in global scope */}
			{isGlobalScope && (
				<Card>
					<CardHeader>
						<div className="flex items-center justify-between">
							<div>
								<CardTitle>ROI by Organization</CardTitle>
								<CardDescription>
									Value delivered to each organization
								</CardDescription>
							</div>
							<Button
								variant="outline"
								size="sm"
								onClick={downloadOrganizationCSV}
								disabled={
									!byOrg?.organizations ||
									byOrg.organizations.length === 0
								}
							>
								<Download className="h-4 w-4 mr-2" />
								Export CSV
							</Button>
						</div>
					</CardHeader>
					<CardContent>
						{isLoading || (!showDemoData && orgLoading) ? (
							<div className="space-y-2">
								<Skeleton className="h-10 w-full" />
								<Skeleton className="h-10 w-full" />
								<Skeleton className="h-10 w-full" />
							</div>
						) : sortedOrganizations.length > 0 ? (
							<DataTable>
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead
											className="cursor-pointer select-none transition-colors hover:bg-muted/50"
											onClick={() =>
												toggleOrgSort("name")
											}
										>
											<div className="flex items-center gap-1">
												Organization
												{orgSort.by === "name" &&
													(orgSort.dir === "desc" ? (
														<ChevronDown className="h-4 w-4" />
													) : (
														<ChevronUp className="h-4 w-4" />
													))}
											</div>
										</DataTableHead>
										<DataTableHead
											className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
											onClick={() =>
												toggleOrgSort("executions")
											}
										>
											<div className="flex items-center justify-end gap-1">
												Executions
												{orgSort.by === "executions" &&
													(orgSort.dir === "desc" ? (
														<ChevronDown className="h-4 w-4" />
													) : (
														<ChevronUp className="h-4 w-4" />
													))}
											</div>
										</DataTableHead>
										<DataTableHead
											className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
											onClick={() =>
												toggleOrgSort("time")
											}
										>
											<div className="flex items-center justify-end gap-1">
												Time Saved (hrs)
												{orgSort.by === "time" &&
													(orgSort.dir === "desc" ? (
														<ChevronDown className="h-4 w-4" />
													) : (
														<ChevronUp className="h-4 w-4" />
													))}
											</div>
										</DataTableHead>
										<DataTableHead
											className="text-right cursor-pointer select-none transition-colors hover:bg-muted/50"
											onClick={() =>
												toggleOrgSort("value")
											}
										>
											<div className="flex items-center justify-end gap-1">
												Value
												{orgSort.by === "value" &&
													(orgSort.dir === "desc" ? (
														<ChevronDown className="h-4 w-4" />
													) : (
														<ChevronUp className="h-4 w-4" />
													))}
											</div>
										</DataTableHead>
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{sortedOrganizations.map((org) => (
										<DataTableRow key={org.organization_id}>
											<DataTableCell className="font-medium">
												{org.organization_name}
											</DataTableCell>
											<DataTableCell className="text-right">
												{org.execution_count.toLocaleString()}
											</DataTableCell>
											<DataTableCell className="text-right">
												{(
													org.total_time_saved / 60
												).toFixed(2)}
											</DataTableCell>
											<DataTableCell className="text-right">
												{org.total_value.toLocaleString(
													"en-US",
													{
														style: "currency",
														currency: "USD",
													},
												)}
											</DataTableCell>
										</DataTableRow>
									))}
								</DataTableBody>
							</DataTable>
						) : (
							<div className="flex items-center justify-center py-8 text-muted-foreground">
								No organization data available for this period
							</div>
						)}
					</CardContent>
				</Card>
			)}
		</div>
	);
}
