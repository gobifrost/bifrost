import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
	Workflow,
	FileText,
	TrendingUp,
	AlertCircle,
	CheckCircle2,
	Zap,
	Clock,
	DollarSign,
	RefreshCw,
	ServerCog,
} from "lucide-react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useDashboardMetrics } from "@/hooks/useDashboardMetrics";
import { useAuth } from "@/contexts/AuthContext";
import {
	useResourceMetrics,
	useOrganizationMetrics,
	useWorkflowMetrics,
} from "@/hooks/useAdminMetrics";
import {
	ResourceTrendChart,
	ExecutionsByOrgChart,
	HeaviestWorkflowsTable,
} from "@/components/charts";

/**
 * Format time saved in minutes to a human-readable string.
 */
function formatTimeSaved(minutes: number): string {
	const hours = Math.floor(minutes / 60);
	const mins = minutes % 60;
	if (hours > 0) {
		return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
	}
	return `${mins}m`;
}

/**
 * Format value with commas for thousands separator.
 */
function formatValue(value: number): string {
	return value.toLocaleString("en-US", {
		minimumFractionDigits: 0,
		maximumFractionDigits: 2,
	});
}

export function Dashboard() {
	const navigate = useNavigate();
	const { isPlatformAdmin, isOrgUser } = useAuth();
	const { data: metrics, isLoading, error, refetch: refetchDashboard, isFetching } = useDashboardMetrics();

	// Admin-only metrics hooks
	const [workflowSort, setWorkflowSort] = useState<
		"executions" | "memory" | "duration" | "cpu"
	>("memory");

	const { data: resourceData, isLoading: resourceLoading, refetch: refetchResource } =
		useResourceMetrics(7, isPlatformAdmin);
	const { data: orgData, isLoading: orgLoading, refetch: refetchOrg } = useOrganizationMetrics(
		30,
		10,
		isPlatformAdmin,
	);
	const { data: workflowData, isLoading: workflowLoading, refetch: refetchWorkflow } =
		useWorkflowMetrics(30, workflowSort, 20, isPlatformAdmin);

	const handleRefresh = () => {
		refetchDashboard();
		if (isPlatformAdmin) {
			refetchResource();
			refetchOrg();
			refetchWorkflow();
		}
	};

	// Redirect OrgUsers to /forms (their only accessible page)
	if (isOrgUser && !isPlatformAdmin) {
		navigate("/forms", { replace: true });
		return null;
	}

	if (error) {
		return (
			<div className="space-y-6">
				<div>
					<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
						Dashboard
					</h1>
					<p className="leading-7 mt-2 text-muted-foreground">
						Platform overview and metrics
					</p>
				</div>

				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						Failed to load dashboard metrics. Please try again
						later.
					</AlertDescription>
				</Alert>
			</div>
		);
	}

	return (
		<div className="space-y-4">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
						Dashboard
					</h1>
					<p className="leading-7 mt-2 text-muted-foreground">
						Platform overview and metrics
					</p>
				</div>
				<Button
					variant="outline"
					size="icon"
					onClick={handleRefresh}
					disabled={isFetching}
				>
					<RefreshCw
						className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
					/>
				</Button>
			</div>

			{/* Metrics Cards */}
			<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
				{/* Workflows */}
				<Link to="/workflows" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Workflows
							</CardTitle>
							<Workflow className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.workflow_count ?? 0}
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								Available workflows
							</p>
						</CardContent>
					</Card>
				</Link>

				{/* Forms */}
				<Link to="/forms" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Forms
							</CardTitle>
							<FileText className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.form_count ?? 0}
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								Active forms
							</p>
						</CardContent>
					</Card>
				</Link>

				{/* Total Executions (30 days) */}
				<Link to="/history" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Executions (30d)
							</CardTitle>
							<Zap className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.execution_stats.total_executions ?? 0}
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								Total workflow runs
							</p>
						</CardContent>
					</Card>
				</Link>

				{/* Success Rate */}
				<Link to="/history" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Success Rate
							</CardTitle>
							<TrendingUp className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.execution_stats.success_rate?.toFixed(
										1,
									) ?? 0}
									%
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								Last 30 days
							</p>
						</CardContent>
					</Card>
				</Link>

				{/* Time Saved (24h) */}
				<Link to="/reports/roi" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Time Saved (24h)
							</CardTitle>
							<Clock className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.roi_24h
										? formatTimeSaved(
												metrics.roi_24h.total_time_saved,
											)
										: "0m"}
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								{metrics?.roi_24h?.time_saved_unit ?? "minutes"}
							</p>
						</CardContent>
					</Card>
				</Link>

				{/* Value Generated (24h) */}
				<Link to="/reports/roi" className="block">
					<Card className="hover:border-primary/50 transition-colors cursor-pointer">
						<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
							<CardTitle className="text-sm font-medium">
								Value (24h)
							</CardTitle>
							<DollarSign className="h-4 w-4 text-muted-foreground" />
						</CardHeader>
						<CardContent>
							{isLoading ? (
								<Skeleton className="h-8 w-16" />
							) : (
								<div className="text-2xl font-bold">
									{metrics?.roi_24h
										? formatValue(metrics.roi_24h.total_value)
										: "0"}
								</div>
							)}
							<p className="text-xs text-muted-foreground">
								{metrics?.roi_24h?.value_unit ?? "USD"}
							</p>
						</CardContent>
					</Card>
				</Link>
			</div>

			{/* Recent Failures - Compact */}
			<Card>
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<div>
							<CardTitle className="text-base">
								Recent Failures
							</CardTitle>
							<CardDescription className="text-xs">
								Last 30 days
							</CardDescription>
						</div>
						<div className="flex items-center gap-2">
							<Link
								to="/history?status=failed"
								className="text-xs text-muted-foreground hover:text-foreground transition-colors"
							>
								View all
							</Link>
							<Badge
								variant={
									metrics?.execution_stats.failed_count === 0
										? "outline"
										: "destructive"
								}
							>
								{isLoading
									? "..."
									: (metrics?.execution_stats.failed_count ??
										0)}{" "}
								Failed
							</Badge>
							<Badge variant="default" className="bg-green-500">
								{isLoading
									? "..."
									: (metrics?.execution_stats.success_count ??
										0)}{" "}
								Success
							</Badge>
						</div>
					</div>
				</CardHeader>
				<CardContent>
					{isLoading ? (
						<div className="space-y-2">
							<Skeleton className="h-16 w-full" />
							<Skeleton className="h-16 w-full" />
						</div>
					) : metrics && metrics.recent_failures.length > 0 ? (
						<div className="space-y-2">
							{metrics.recent_failures
								.slice(0, 5)
								.map((failure) => (
									<div
										key={failure.execution_id}
										className="flex items-start justify-between p-2 rounded-md border hover:bg-muted/50 cursor-pointer gap-2"
										onClick={() =>
											navigate(
												`/history/${failure.execution_id}`,
											)
										}
									>
										<div className="flex-1 min-w-0 overflow-hidden">
											<div className="flex items-center gap-2">
												<AlertCircle className="h-4 w-4 text-destructive flex-shrink-0" />
												<span className="font-mono text-xs font-medium break-words">
													{failure.workflow_name}
												</span>
											</div>
											{failure.error_message && (
												<p className="text-xs text-muted-foreground ml-6 break-words">
													{failure.error_message}
												</p>
											)}
										</div>
										{failure.started_at && (
											<span className="text-xs text-muted-foreground flex-shrink-0 whitespace-nowrap">
												{new Date(
													failure.started_at,
												).toLocaleDateString()}
											</span>
										)}
									</div>
								))}
						</div>
					) : (
						<div className="flex items-center justify-center py-4 text-center">
							<CheckCircle2 className="h-8 w-8 text-green-500 mr-2" />
							<p className="text-sm text-muted-foreground">
								All executions successful
							</p>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Platform Analytics - Admin Only */}
			{isPlatformAdmin && (
				<div className="space-y-4">
					<h2 className="text-xl font-semibold">
						Platform Analytics
					</h2>

					<Link to="/infrastructure" className="block">
						<Card className="hover:border-primary/50 transition-colors cursor-pointer">
							<CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
								<div>
									<CardTitle className="text-base">
										Infrastructure Status
									</CardTitle>
									<CardDescription className="text-xs">
										Read-only instance map
									</CardDescription>
								</div>
								<ServerCog className="h-4 w-4 text-muted-foreground" />
							</CardHeader>
							<CardContent>
								<div className="flex flex-wrap items-center gap-2">
									<Badge variant="outline">View live status</Badge>
								</div>
								<p className="mt-2 text-xs text-muted-foreground">
									Deployment, host, API, execution, adjacent
									services, and external integrations
								</p>
							</CardContent>
						</Card>
					</Link>

					{/* Resource Trend Chart - Full Width */}
					<ResourceTrendChart
						data={resourceData?.days || []}
						isLoading={resourceLoading}
					/>

					{/* Two-column layout for org chart and workflow table */}
					<div className="grid gap-4 lg:grid-cols-2">
						<ExecutionsByOrgChart
							data={orgData?.organizations || []}
							isLoading={orgLoading}
						/>
						<HeaviestWorkflowsTable
							data={workflowData?.workflows || []}
							isLoading={workflowLoading}
							sortBy={workflowSort}
							onSortChange={setWorkflowSort}
						/>
					</div>
				</div>
			)}
		</div>
	);
}
