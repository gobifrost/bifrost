import { useState, useMemo } from "react";
import { format, subDays } from "date-fns";
import type { DateRange } from "react-day-picker";
import { AlertCircle, Sparkles } from "lucide-react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	useUsageReport,
	type UsageReportResponse,
	type UsageSource,
	type WorkflowUsage,
	type ConversationUsage,
	type OrganizationUsage,
	type UsageTrend,
} from "@/services/usage";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { UsageSummaryCards } from "@/components/reports/UsageSummaryCards";
import { UsageCharts } from "@/components/reports/UsageCharts";
import { UsageLimitsPanel } from "@/components/reports/UsageLimitsPanel";
import {
	WorkflowTable,
	ConversationTable,
	AgentTable,
	OrganizationTable,
	KnowledgeStorageTable,
} from "@/components/reports/UsageTables";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

// ============================================================================
// Demo Data Generation
// ============================================================================

// Extended types for demo data
type DemoWorkflowUsage = WorkflowUsage & { organization_id: string };
type DemoConversationUsage = ConversationUsage & { organization_id: string };

// Demo workflow templates
const DEMO_WORKFLOW_TEMPLATES = [
	{
		name: "User Onboarding",
		inputTokens: 45000,
		outputTokens: 12000,
		cost: 0.85,
		cpuSeconds: 120,
		memoryBytes: 256 * 1024 * 1024,
		baseCount: 280,
	},
	{
		name: "Ticket Triage",
		inputTokens: 22000,
		outputTokens: 8000,
		cost: 0.42,
		cpuSeconds: 45,
		memoryBytes: 128 * 1024 * 1024,
		baseCount: 450,
	},
	{
		name: "Invoice Processing",
		inputTokens: 35000,
		outputTokens: 15000,
		cost: 0.68,
		cpuSeconds: 90,
		memoryBytes: 192 * 1024 * 1024,
		baseCount: 190,
	},
	{
		name: "Compliance Check",
		inputTokens: 78000,
		outputTokens: 25000,
		cost: 1.45,
		cpuSeconds: 180,
		memoryBytes: 384 * 1024 * 1024,
		baseCount: 75,
	},
	{
		name: "Data Backup Verification",
		inputTokens: 18000,
		outputTokens: 5000,
		cost: 0.32,
		cpuSeconds: 60,
		memoryBytes: 96 * 1024 * 1024,
		baseCount: 210,
	},
	{
		name: "Report Generation",
		inputTokens: 55000,
		outputTokens: 30000,
		cost: 1.12,
		cpuSeconds: 150,
		memoryBytes: 320 * 1024 * 1024,
		baseCount: 70,
	},
];

// Demo conversation templates
const DEMO_CONVERSATION_TEMPLATES = [
	{
		title: "Help with deployment",
		messages: 12,
		inputTokens: 8500,
		outputTokens: 4200,
		cost: 0.18,
	},
	{
		title: "Database migration questions",
		messages: 8,
		inputTokens: 5200,
		outputTokens: 3100,
		cost: 0.12,
	},
	{
		title: "API integration support",
		messages: 15,
		inputTokens: 11000,
		outputTokens: 6500,
		cost: 0.25,
	},
	{
		title: "Security audit discussion",
		messages: 6,
		inputTokens: 4000,
		outputTokens: 2200,
		cost: 0.09,
	},
	{
		title: "Performance optimization",
		messages: 10,
		inputTokens: 7500,
		outputTokens: 4800,
		cost: 0.17,
	},
	{
		title: "New feature planning",
		messages: 20,
		inputTokens: 15000,
		outputTokens: 9000,
		cost: 0.34,
	},
];

// Fallback demo orgs
const FALLBACK_DEMO_ORGS = [
	{ id: "demo-org-1", name: "Acme Corp" },
	{ id: "demo-org-2", name: "TechStart Inc" },
	{ id: "demo-org-3", name: "Global Services LLC" },
];

interface DemoDataParams {
	startDate: string;
	endDate: string;
	orgId: string | null;
	source: UsageSource;
	realOrgs: Array<{ id: string; name: string }> | undefined;
}

/**
 * Generate demo data that mirrors API response structure.
 */
function generateDemoData(params: DemoDataParams): UsageReportResponse {
	const { startDate, endDate, orgId, source, realOrgs } = params;
	const orgs =
		realOrgs && realOrgs.length > 0 ? realOrgs : FALLBACK_DEMO_ORGS;

	// Generate workflows with org assignments
	const allWorkflows: DemoWorkflowUsage[] = DEMO_WORKFLOW_TEMPLATES.map(
		(template, index) => {
			const org = orgs[index % orgs.length];
			const variance = 0.9 + Math.random() * 0.2;
			const executions = Math.floor(template.baseCount * variance);

			return {
				workflow_name: template.name,
				organization_id: org.id,
				execution_count: executions,
				input_tokens: Math.floor(
					template.inputTokens * executions * variance,
				),
				output_tokens: Math.floor(
					template.outputTokens * executions * variance,
				),
				ai_cost: (template.cost * executions * variance).toFixed(2),
				cpu_seconds: Math.floor(
					template.cpuSeconds * executions * variance,
				),
				memory_bytes: template.memoryBytes,
			};
		},
	);

	// Generate conversations with org assignments
	const allConversations: DemoConversationUsage[] =
		DEMO_CONVERSATION_TEMPLATES.map((template, index) => {
			const org = orgs[index % orgs.length];
			const variance = 0.85 + Math.random() * 0.3;

			return {
				conversation_id: `demo-conv-${index + 1}`,
				conversation_title: template.title,
				organization_id: org.id,
				message_count: Math.floor(template.messages * variance),
				input_tokens: Math.floor(template.inputTokens * variance),
				output_tokens: Math.floor(template.outputTokens * variance),
				ai_cost: (template.cost * variance).toFixed(2),
			};
		});

	// Filter by org
	const filteredWorkflows = orgId
		? allWorkflows.filter((w) => w.organization_id === orgId)
		: allWorkflows;

	const filteredConversations = orgId
		? allConversations.filter((c) => c.organization_id === orgId)
		: allConversations;

	// Filter by source
	const includeWorkflows = source === "all" || source === "executions";
	const includeConversations = source === "all" || source === "chat";

	// Calculate summary from filtered data
	let totalInputTokens = 0;
	let totalOutputTokens = 0;
	let totalAiCost = 0;
	let totalCpuSeconds = 0;
	let peakMemoryBytes = 0;
	let totalAiCalls = 0;

	if (includeWorkflows) {
		for (const w of filteredWorkflows) {
			totalInputTokens += w.input_tokens;
			totalOutputTokens += w.output_tokens;
			totalAiCost += parseFloat(w.ai_cost || "0");
			totalCpuSeconds += w.cpu_seconds;
			peakMemoryBytes = Math.max(peakMemoryBytes, w.memory_bytes);
			totalAiCalls += w.execution_count;
		}
	}

	if (includeConversations) {
		for (const c of filteredConversations) {
			totalInputTokens += c.input_tokens;
			totalOutputTokens += c.output_tokens;
			totalAiCost += parseFloat(c.ai_cost || "0");
			totalAiCalls += c.message_count;
		}
	}

	// Generate trends
	const start = new Date(startDate);
	const end = new Date(endDate);
	const dayCount = Math.max(
		1,
		Math.ceil((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)) +
			1,
	);

	const dailyCost = totalAiCost / dayCount;
	const dailyInputTokens = totalInputTokens / dayCount;
	const dailyOutputTokens = totalOutputTokens / dayCount;

	const trends: UsageTrend[] = [];
	const currentDate = new Date(start);
	let dayIndex = 0;

	while (currentDate <= end) {
		const isWeekend =
			currentDate.getDay() === 0 || currentDate.getDay() === 6;
		const baseMultiplier = isWeekend ? 0.5 : 1;
		const trendMultiplier = 1 + dayIndex * 0.003;
		const variance = 0.85 + Math.random() * 0.3;
		const dayFactor = baseMultiplier * trendMultiplier * variance;

		trends.push({
			date: format(currentDate, "yyyy-MM-dd"),
			ai_cost: (dailyCost * dayFactor).toFixed(2),
			input_tokens: Math.round(dailyInputTokens * dayFactor),
			output_tokens: Math.round(dailyOutputTokens * dayFactor),
		});

		currentDate.setDate(currentDate.getDate() + 1);
		dayIndex++;
	}

	// Build by-organization data
	const orgMetrics = new Map<
		string,
		{
			name: string;
			execution_count: number;
			conversation_count: number;
			input_tokens: number;
			output_tokens: number;
			ai_cost: number;
		}
	>();

	for (const workflow of allWorkflows) {
		const existing = orgMetrics.get(workflow.organization_id);
		const orgName =
			orgs.find((o) => o.id === workflow.organization_id)?.name ??
			"Unknown";

		if (existing) {
			existing.execution_count += workflow.execution_count;
			existing.input_tokens += workflow.input_tokens;
			existing.output_tokens += workflow.output_tokens;
			existing.ai_cost += parseFloat(workflow.ai_cost || "0");
		} else {
			orgMetrics.set(workflow.organization_id, {
				name: orgName,
				execution_count: workflow.execution_count,
				conversation_count: 0,
				input_tokens: workflow.input_tokens,
				output_tokens: workflow.output_tokens,
				ai_cost: parseFloat(workflow.ai_cost || "0"),
			});
		}
	}

	for (const conv of allConversations) {
		const existing = orgMetrics.get(conv.organization_id);
		if (existing) {
			existing.conversation_count += 1;
			existing.input_tokens += conv.input_tokens;
			existing.output_tokens += conv.output_tokens;
			existing.ai_cost += parseFloat(conv.ai_cost || "0");
		}
	}

	const byOrganization: OrganizationUsage[] = Array.from(
		orgMetrics.entries(),
	).map(([id, metrics]) => ({
		organization_id: id,
		organization_name: metrics.name,
		execution_count: metrics.execution_count,
		conversation_count: metrics.conversation_count,
		input_tokens: metrics.input_tokens,
		output_tokens: metrics.output_tokens,
		ai_cost: metrics.ai_cost.toFixed(2),
	}));

	return {
		summary: {
			total_ai_cost: totalAiCost.toFixed(2),
			total_input_tokens: totalInputTokens,
			total_output_tokens: totalOutputTokens,
			total_ai_calls: totalAiCalls,
			total_cpu_seconds: totalCpuSeconds,
			peak_memory_bytes: peakMemoryBytes,
		},
		trends,
		by_workflow: includeWorkflows ? filteredWorkflows : undefined,
		by_conversation: includeConversations
			? filteredConversations
			: undefined,
		by_organization: byOrganization,
	};
}

// ============================================================================
// Component
// ============================================================================

export function UsageReports() {
	const { selectedBoundary, selectedTarget, hasSelectedCapability } =
		useAuthorizationBoundary();
	const selectedOrgId = selectedBoundary?.startsWith("organization:")
		? selectedBoundary.slice("organization:".length)
		: null;
	const isManagedBoundary = selectedTarget?.kind === "managed_organizations";
	const canEditMetrics = hasSelectedCapability("metrics.readwrite");
	const canReadOrganizations = hasSelectedCapability("organizations.read");
	const [activeTab, setActiveTab] = useState("overview");

	// Organization filter state (platform admins only)
	// undefined = all, null = global only, UUID string = specific org
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const effectiveFilterOrgId =
		selectedBoundary === "platform" ? filterOrgId : selectedOrgId;

	// Derive isGlobalScope from filterOrgId for display logic
	const isGlobalScope =
		selectedBoundary === "platform" &&
		(effectiveFilterOrgId === undefined || effectiveFilterOrgId === null);

	// Fetch real organizations for demo data generation
	const { data: orgsData } = useOrganizations({
		enabled: selectedBoundary === "platform" && canReadOrganizations,
		boundary: selectedBoundary,
	});

	// Demo mode state
	const [showDemoData, setShowDemoData] = useState(false);

	// Source filter (Executions | Chat | All)
	const [source, setSource] = useState<UsageSource>("all");

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
		return generateDemoData({
			startDate,
			endDate,
			orgId: effectiveFilterOrgId ?? null,
			source,
			realOrgs,
		});
	}, [startDate, endDate, effectiveFilterOrgId, source, realOrgs]);

	// Fetch real data
	const {
		data: realData,
		isLoading,
		error,
	} = useUsageReport(startDate, endDate, source, effectiveFilterOrgId, {
		enabled: activeTab === "overview" && !isManagedBoundary,
	});

	// Use demo or real data
	const data = showDemoData ? demoData : realData;

	// Loading state
	const isLoadingData = showDemoData ? false : isLoading;

	// Error state
	const hasError = showDemoData ? false : !!error;

	// Show conversation table when source is chat or all
	const showConversationTable = source === "chat" || source === "all";

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="flex items-start justify-between">
				<div>
					<div className="flex items-center gap-3">
						<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
							Usage
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
						Usage analytics and portable limits for the active working
						context.
					</p>
				</div>

				{/* Demo Data Toggle - only for contexts that can manage metrics */}
				{canEditMetrics && (
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
						Toggle off to view real usage data.
					</AlertDescription>
				</Alert>
			)}

			<Tabs value={activeTab} onValueChange={setActiveTab}>
				<TabsList>
					<TabsTrigger value="overview">Overview</TabsTrigger>
					<TabsTrigger value="limits">Limits</TabsTrigger>
				</TabsList>

				<TabsContent value="overview" className="mt-6 space-y-6">
					{isManagedBoundary ? (
						<Alert>
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>
								Managed organizations is a browse view. Choose Global or one
								exact organization from Working in to view usage reports.
							</AlertDescription>
						</Alert>
					) : (
						<>
							{/* Filters: Date Range, Source Tabs, and Organization */}
							<Card>
								<CardHeader>
									<CardTitle>Report period</CardTitle>
									<CardDescription>
										Select a date range and source for the usage report.
										{selectedOrgId
											? " This report is locked to the selected organization."
											: ""}
									</CardDescription>
								</CardHeader>
								<CardContent className="space-y-4">
									<DateRangePicker
										dateRange={dateRange}
										onDateRangeChange={setDateRange}
									/>

									<div className="flex flex-col gap-4 lg:flex-row lg:items-center">
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
										{selectedBoundary === "platform" &&
											canReadOrganizations && (
											<div className="w-full lg:ml-auto lg:w-64">
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
										Failed to load usage data. Please try again later.
									</AlertDescription>
								</Alert>
							)}

							<UsageSummaryCards data={data} isLoading={isLoadingData} />
							<UsageCharts trends={data?.trends} isLoading={isLoadingData} />

							{(source === "all" || source === "executions") && (
								<WorkflowTable
									workflows={data?.by_workflow}
									isLoading={isLoadingData}
									startDate={startDate}
									endDate={endDate}
									isDemo={showDemoData}
								/>
							)}

							{showConversationTable && (
								<ConversationTable
									conversations={data?.by_conversation}
									isLoading={isLoadingData}
									startDate={startDate}
									endDate={endDate}
									isDemo={showDemoData}
								/>
							)}

							{(source === "all" || source === "agents") &&
								data?.by_agent &&
								data.by_agent.length > 0 && (
									<AgentTable
										agents={data.by_agent}
										isLoading={isLoadingData}
									/>
								)}

							{isGlobalScope && (
								<OrganizationTable
									organizations={data?.by_organization}
									isLoading={isLoadingData}
									startDate={startDate}
									endDate={endDate}
									isDemo={showDemoData}
								/>
							)}

							<KnowledgeStorageTable
								data={data}
								isLoading={isLoadingData}
								startDate={startDate}
								isDemo={showDemoData}
							/>
						</>
					)}
				</TabsContent>

				<TabsContent value="limits" className="mt-6">
					<UsageLimitsPanel />
				</TabsContent>
			</Tabs>
		</div>
	);
}
