import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { AlertCircle, RefreshCw } from "lucide-react";
import { WorkspaceTabs } from "@/components/layout/WorkspaceTabs";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useDashboardMetrics } from "@/hooks/useDashboardMetrics";
import { useAuth } from "@/contexts/AuthContext";
import { useExecutionTimeSeries } from "@/hooks/useExecutionTimeSeries";
import {
	ExecutionsOverTimeCard,
	WINDOW_LABELS,
} from "@/components/dashboard/ExecutionsOverTimeCard";
import { DashboardStatCards } from "@/components/dashboard/DashboardStatCards";
import type { ChartWindow, OutcomeSummary } from "@/lib/execution-buckets";
import { $api } from "@/lib/api-client";

export function Dashboard() {
	const { isPlatformAdmin, isOrgUser } = useAuth();
	const {
		data: metrics,
		isLoading,
		error,
		refetch: refetchDashboard,
		isFetching,
	} = useDashboardMetrics();

	const [chartWindow, setChartWindow] = useState<ChartWindow>("7d");
	const {
		data: executionTimeSeries,
		isLoading: executionsLoading,
		isError: executionsError,
		isFetching: executionsFetching,
		refetch: refetchExecutions,
	} = useExecutionTimeSeries(chartWindow);

	const outcomes: OutcomeSummary = useMemo(
		() => ({
			success: executionTimeSeries?.success_count ?? 0,
			failed: executionTimeSeries?.failed_count ?? 0,
			total: executionTimeSeries?.total_count ?? 0,
			successRate: executionTimeSeries?.success_rate ?? null,
		}),
		[executionTimeSeries],
	);

	const {
		data: agentsData,
		isLoading: agentsLoading,
		isError: agentsError,
		isFetching: agentsFetching,
		refetch: refetchAgents,
	} = $api.useQuery("get", "/api/agents", {}, { staleTime: 60000 });
	const {
		data: appsData,
		isLoading: appsLoading,
		isError: appsError,
		isFetching: appsFetching,
		refetch: refetchApps,
	} = $api.useQuery("get", "/api/applications", {}, { staleTime: 60000 });

	const refreshing =
		isFetching || executionsFetching || agentsFetching || appsFetching;
	const handleRefresh = () => {
		refetchDashboard();
		refetchExecutions();
		refetchAgents();
		refetchApps();
	};

	// Redirect OrgUsers to /forms (their only accessible page)
	if (isOrgUser && !isPlatformAdmin) {
		return <Navigate to="/forms" replace />;
	}

	return (
		<PageWorkspace className="mx-auto flex min-w-0 max-w-[1400px] flex-col gap-6">
			<div className="shrink-0 space-y-6">
				<ListPageHeader
					title="Dashboard"
					titleSlot={<WorkspaceTabs />}
					description="Platform overview and metrics"
					actions={
						<Button
							type="button"
							variant="outline"
							size="icon"
							onClick={handleRefresh}
							disabled={refreshing}
							aria-label="Refresh dashboard"
							className="size-11 sm:size-9"
						>
							<RefreshCw
								className={`h-4 w-4 ${refreshing ? "animate-spin motion-reduce:animate-none" : ""}`}
							/>
						</Button>
					}
				/>
			</div>
			<PageScrollArea className="space-y-6">
				{(error || agentsError || appsError) && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							Couldn't load{" "}
							{[
								error && "platform metrics",
								agentsError && "agent inventory",
								appsError && "app inventory",
							]
								.filter(Boolean)
								.join(", ")}
							. Refresh to try again.
						</AlertDescription>
					</Alert>
				)}

				{/* Headline numbers paired with the chart, inventory, and value */}
				<DashboardStatCards
					windowLabel={WINDOW_LABELS[chartWindow]}
					outcomes={outcomes}
					executionsLoading={executionsLoading}
					executionsError={executionsError}
					inventory={{
						workflows: metrics?.workflow_count ?? 0,
						forms: metrics?.form_count ?? 0,
						agents: agentsData?.length ?? 0,
						apps: appsData?.total ?? 0,
					}}
					inventoryLoading={isLoading || agentsLoading || appsLoading}
					inventoryError={Boolean(error) || agentsError || appsError}
					roi={
						metrics?.roi_24h
							? {
									timeSavedMinutes:
										metrics.roi_24h.total_time_saved,
									value: metrics.roi_24h.total_value,
									valueUnit: metrics.roi_24h.value_unit,
								}
							: undefined
					}
					roiLoading={isLoading}
					roiError={Boolean(error)}
				/>

				{/* Executions over time — successes and failures overlaid */}
				<ExecutionsOverTimeCard
					window={chartWindow}
					onWindowChange={setChartWindow}
					buckets={executionTimeSeries?.buckets}
					outcomes={outcomes}
					isLoading={executionsLoading}
					isError={executionsError}
				/>
			</PageScrollArea>
		</PageWorkspace>
	);
}
