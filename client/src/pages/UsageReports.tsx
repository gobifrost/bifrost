import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { Button } from "@/components/ui/button";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { useState, useMemo } from "react";
import { format, subDays } from "date-fns";
import type { DateRange } from "react-day-picker";
import { AlertCircle, Sparkles } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	useUsageBreakdown,
	useUsageReport,
	type UsageBreakdownFilters,
	type UsageSource,
} from "@/services/usage";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { UsageSummaryCards } from "@/components/reports/UsageSummaryCards";
import { UsageCharts } from "@/components/reports/UsageCharts";
import {
	TestingReviewBreakdown,
	type TestingReviewFilters,
} from "@/components/reports/TestingReviewBreakdown";
import { generateUsageDemoData } from "./UsageReports.demo";
import {
	WorkflowTable,
	ConversationTable,
	AgentTable,
	OrganizationTable,
	KnowledgeStorageTable,
} from "@/components/reports/UsageTables";

// ============================================================================
// Component
// ============================================================================

export function UsageReports() {
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

	// Source filter (Executions | Chat | All)
	const [source, setSource] = useState<UsageSource>("all");
	const [qualityFilters, setQualityFilters] =
		useState<TestingReviewFilters>({});

	// Default to last 30 days
	const [dateRange, setDateRange] = useState<DateRange | undefined>({
		from: subDays(new Date(), 30),
		to: new Date(),
	});

	// Format dates for API (YYYY-MM-DD)
	const startDate = dateRange?.from
		? format(dateRange.from, "yyyy-MM-dd")
		: "";
	const endDate = dateRange?.to ? format(dateRange.to, "yyyy-MM-dd") : "";

	// Memoize orgs array for stable reference
	const realOrgs = useMemo(() => {
		if (!orgsData || !Array.isArray(orgsData)) return undefined;
		return orgsData.map((o) => ({ id: o.id, name: o.name }));
	}, [orgsData]);

	// Demo data - pre-filtered like API
	// Use filterOrgId for filtering (undefined/null means show all orgs)
	const demoData = useMemo(() => {
		if (!startDate || !endDate) return null;
		return generateUsageDemoData({
			startDate,
			endDate,
			orgId: filterOrgId ?? null,
			source,
			realOrgs,
		});
	}, [startDate, endDate, filterOrgId, source, realOrgs]);

	// Fetch real data
	const {
		data: realData,
		isLoading,
		error,
		refetch,
		isFetching,
	} = useUsageReport(startDate, endDate, source, filterOrgId);

	const usageBreakdownFilters = useMemo(() => {
		const filters: UsageBreakdownFilters = {
			start_date: startDate,
			end_date: endDate,
			source,
			limit: 50,
			offset: 0,
		};
		if (typeof filterOrgId === "string") filters.org_id = filterOrgId;
		if (filterOrgId === null) filters.global_only = true;
		for (const key of [
			"purpose",
			"provider",
			"model",
			"profile_id",
			"profile_fingerprint",
		] as const) {
			const value = qualityFilters[key];
			if (value) filters[key] = value;
		}
		return filters;
	}, [endDate, filterOrgId, qualityFilters, source, startDate]);

	const {
		data: usageBreakdown,
		isLoading: isUsageBreakdownLoading,
		error: usageBreakdownError,
		refetch: refetchUsageBreakdown,
		isFetching: isUsageBreakdownFetching,
	} = useUsageBreakdown(usageBreakdownFilters);

	// Use demo or real data
	const data = showDemoData ? demoData : realData;

	// Loading state
	const isLoadingData = showDemoData ? false : isLoading;

	// Error state
	const hasError = showDemoData ? false : !!error;

	// Show conversation table when source is chat or all
	const showConversationTable = source === "chat" || source === "all";

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1440px] min-w-0">
			<div className="shrink-0 space-y-4">
				<ListPageHeader
					title="Usage Reports"
					description="AI usage and resource consumption analytics"
					actions={
						isPlatformAdmin && (
							<div className="flex min-h-11 flex-wrap items-center gap-3">
								{showDemoData && (
									<Badge variant="outline">Demo Mode</Badge>
								)}
								<Switch
									id="demo-mode"
									checked={showDemoData}
									onCheckedChange={setShowDemoData}
								/>
								<Label
									htmlFor="demo-mode"
									className="flex min-h-11 cursor-pointer items-center text-sm text-muted-foreground"
								>
									Show Demo Data
								</Label>
							</div>
						)
					}
				/>

				{/* Demo Mode Banner */}
				{showDemoData && (
					<Alert className="rounded-[var(--bf-radius-surface)] border-border bg-muted/40">
						<Sparkles className="h-4 w-4 text-muted-foreground" />
						<AlertDescription className="text-muted-foreground">
							Displaying sample data for demonstration purposes.
							Toggle off to view real usage data.
						</AlertDescription>
					</Alert>
				)}

				{/* Filters: Date Range, Source Tabs, and Organization */}
				<section
					aria-label="Report filters"
					className="flex min-w-0 flex-wrap items-center gap-3 border-b pb-4"
				>
					<div className="w-full sm:w-auto sm:max-w-sm">
						<DateRangePicker
							dateRange={dateRange}
							onDateRangeChange={setDateRange}
						/>
					</div>

					<div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
						<Label className="text-sm font-medium">Source:</Label>
						<Tabs
							value={source}
							onValueChange={(v) => setSource(v as UsageSource)}
						>
							<TabsList>
								<TabsTrigger value="all">All</TabsTrigger>
								<TabsTrigger value="executions">
									Executions
								</TabsTrigger>
								<TabsTrigger value="chat">Chat</TabsTrigger>
								<TabsTrigger value="agents">Agents</TabsTrigger>
							</TabsList>
						</Tabs>
						{isPlatformAdmin && (
							<div className="w-full sm:ml-auto sm:w-56">
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
				</section>
			</div>

			{/* Error Alert */}
			<PageScrollArea className="space-y-6">
				{hasError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription className="flex flex-col items-start gap-3">
							<span>
								{data
									? "Usage data could not be refreshed. Showing the last loaded report."
									: "Usage data could not be loaded. Try again to retrieve this report."}
							</span>
							<Button
								type="button"
								variant="outline"
								className="min-h-11 w-fit"
								disabled={isFetching}
								onClick={() => void refetch()}
							>
								{isFetching ? "Retrying…" : "Retry report"}
							</Button>
						</AlertDescription>
					</Alert>
				)}

				{(!hasError || data) && (
					<>
						{/* Summary Cards */}
						<UsageSummaryCards
							data={data}
							isLoading={isLoadingData}
						/>

						{/* Trends Chart */}
						<UsageCharts
							trends={data?.trends}
							isLoading={isLoadingData}
						/>

						<TestingReviewBreakdown
							data={showDemoData ? undefined : usageBreakdown}
							isLoading={
								showDemoData ? false : isUsageBreakdownLoading
							}
							error={showDemoData ? null : usageBreakdownError}
							isFetching={
								showDemoData ? false : isUsageBreakdownFetching
							}
							onRetry={() => void refetchUsageBreakdown()}
							filters={qualityFilters}
							onFiltersChange={setQualityFilters}
						/>

						{/* By-Workflow Table */}
						{(source === "all" || source === "executions") && (
							<WorkflowTable
								workflows={data?.by_workflow}
								isLoading={isLoadingData}
								startDate={startDate}
								endDate={endDate}
								isDemo={showDemoData}
							/>
						)}

						{/* By-Conversation Table */}
						{showConversationTable && (
							<ConversationTable
								conversations={data?.by_conversation}
								isLoading={isLoadingData}
								startDate={startDate}
								endDate={endDate}
								isDemo={showDemoData}
							/>
						)}

						{/* By-Agent Table */}
						{(source === "all" || source === "agents") && (
							<AgentTable
								agents={data?.by_agent}
								isLoading={isLoadingData}
							/>
						)}

						{/* By-Organization Table - Only shown in global scope */}
						{isGlobalScope && (
							<OrganizationTable
								organizations={data?.by_organization}
								isLoading={isLoadingData}
								startDate={startDate}
								endDate={endDate}
								isDemo={showDemoData}
							/>
						)}

						{/* Knowledge Storage Table */}
						<KnowledgeStorageTable
							data={data}
							isLoading={isLoadingData}
							startDate={startDate}
							isDemo={showDemoData}
						/>
					</>
				)}
			</PageScrollArea>
		</PageWorkspace>
	);
}
