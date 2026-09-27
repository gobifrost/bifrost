import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { Button } from "@/components/ui/button";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { useEffect, useMemo, type ReactNode } from "react";
import { format, isValid, parse, subDays } from "date-fns";
import { useSearchParams } from "react-router-dom";
import type { DateRange } from "react-day-picker";
import { AlertCircle, Sparkles, Workflow as WorkflowIcon } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useUsageReport, type UsageSource } from "@/services/usage";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { UsageSummaryCards } from "@/components/reports/UsageSummaryCards";
import { UsageCharts } from "@/components/reports/UsageCharts";
import { generateUsageDemoData } from "./UsageReports.demo";
import { WorkflowResourcesReport } from "./WorkflowResourcesReport";
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
	const [searchParams, setSearchParams] = useSearchParams();
	const report = searchParams.get("tab") === "workflow" ? "workflow" : "ai";
	const reportSwitch = (
		<ToggleGroup
			type="single"
			value={report}
			onValueChange={(value) => {
				if (!value) return;
				setSearchParams(
					(previous) => {
						const next = new URLSearchParams(previous);
						next.set("tab", value);
						return next;
					},
					{ replace: true },
				);
			}}
			aria-label="Usage report type"
			size="lg"
			className="grid w-full grid-cols-2 justify-start sm:flex sm:w-auto"
		>
			<ToggleGroupItem value="ai" className="gap-1.5">
				<Sparkles className="h-3.5 w-3.5" />
				AI Usage
			</ToggleGroupItem>
			<ToggleGroupItem value="workflow" className="gap-1.5">
				<WorkflowIcon className="h-3.5 w-3.5" />
				Workflow Resources
			</ToggleGroupItem>
		</ToggleGroup>
	);
	return report === "ai" ? (
		<AIUsageReport reportSwitch={reportSwitch} />
	) : (
		<WorkflowResourcesReport reportSwitch={reportSwitch} />
	);
}

function AIUsageReport({ reportSwitch }: { reportSwitch: ReactNode }) {
	const { isPlatformAdmin } = useAuth();
	const [searchParams, setSearchParams] = useSearchParams();
	useEffect(() => {
		if (
			searchParams.get("tab") === "ai" &&
			(searchParams.has("ai_from") || searchParams.has("ai_to"))
		)
			return;
		const today = new Date();
		setSearchParams(
			(previous) => {
				const next = new URLSearchParams(previous);
				next.set("tab", "ai");
				if (!next.has("ai_from") && !next.has("ai_to")) {
					next.set(
						"ai_from",
						format(subDays(today, 30), "yyyy-MM-dd"),
					);
					next.set("ai_to", format(today, "yyyy-MM-dd"));
				}
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

	// Organization filter state (platform admins only)
	// undefined = all, null = global only, UUID string = specific org
	const selectedOrg = searchParams.get("ai_org");
	const filterOrgId =
		selectedOrg === "global" ? null : (selectedOrg ?? undefined);

	// Derive isGlobalScope from filterOrgId for display logic
	const isGlobalScope = filterOrgId === undefined || filterOrgId === null;

	// Fetch real organizations for demo data generation
	const { data: orgsData } = useOrganizations({ enabled: isPlatformAdmin });

	// Demo mode state
	const showDemoData = searchParams.get("ai_demo") === "1";

	// Source filter (Executions | Chat | All)
	const sourceParam = searchParams.get("ai_source");
	const source: UsageSource =
		sourceParam === "executions" ||
		sourceParam === "chat" ||
		sourceParam === "agents"
			? sourceParam
			: "all";

	// Default to last 30 days
	const readDate = (key: string) => {
		const raw = searchParams.get(key);
		if (!raw) return undefined;
		const value = parse(raw, "yyyy-MM-dd", new Date());
		return isValid(value) && format(value, "yyyy-MM-dd") === raw
			? value
			: undefined;
	};
	const selectedFrom = readDate("ai_from");
	const dateRange: DateRange = selectedFrom
		? { from: selectedFrom, to: readDate("ai_to") }
		: { from: subDays(new Date(), 30), to: new Date() };

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

	// Use demo or real data
	const data = showDemoData ? demoData : realData;

	// Loading state
	const isLoadingData = showDemoData ? false : isLoading;

	// Error state
	const hasError = showDemoData ? false : !!error;

	// Show conversation table when source is chat or all
	const showConversationTable = source === "chat" || source === "all";

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1440px] min-w-0 lg:h-auto lg:flex-1">
			<div className="shrink-0 space-y-4">
				<ListPageHeader
					title="Usage"
					titleAccessory={reportSwitch}
					description="Review AI spend, tokens, and storage across the platform."
					actions={
						isPlatformAdmin && (
							<div className="flex min-h-11 flex-wrap items-center gap-3">
								{showDemoData && (
									<Badge variant="outline">Demo Mode</Badge>
								)}
								<Switch
									id="demo-mode"
									checked={showDemoData}
									onCheckedChange={(value) =>
										updateParams({
											ai_demo: value ? "1" : null,
										})
									}
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
					<div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
						<Label className="text-sm font-medium">Source:</Label>
						<Tabs
							value={source}
							onValueChange={(value) =>
								updateParams({
									ai_source: value === "all" ? null : value,
								})
							}
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
									onChange={(value) =>
										updateParams({
											ai_org:
												value === undefined
													? null
													: (value ?? "global"),
										})
									}
									showAll={true}
									showGlobal={true}
									placeholder="All organizations"
								/>
							</div>
						)}
					</div>
					<div className="w-full sm:w-auto sm:max-w-sm">
						<DateRangePicker
							dateRange={dateRange}
							onDateRangeChange={(value) =>
								updateParams({
									ai_from: value?.from
										? format(value.from, "yyyy-MM-dd")
										: null,
									ai_to: value?.to
										? format(value.to, "yyyy-MM-dd")
										: null,
								})
							}
						/>
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
