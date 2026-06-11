import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useDashboardMetrics } from "@/hooks/useDashboardMetrics";
import { useAuth } from "@/contexts/AuthContext";
import { useExecutionsWindow } from "@/hooks/useExecutionsWindow";
import {
	ExecutionsOverTimeCard,
	WINDOW_LABELS,
} from "@/components/dashboard/ExecutionsOverTimeCard";
import { DashboardStatCards } from "@/components/dashboard/DashboardStatCards";
import { summarizeOutcomes, type ChartWindow } from "@/lib/execution-buckets";
import { $api } from "@/lib/api-client";

export function Dashboard() {
	const navigate = useNavigate();
	const { isPlatformAdmin, isOrgUser } = useAuth();
	const { data: metrics, isLoading, error, refetch: refetchDashboard, isFetching } = useDashboardMetrics();

	const [chartWindow, setChartWindow] = useState<ChartWindow>("7d");
	const {
		data: executionsData,
		isLoading: executionsLoading,
		isError: executionsError,
		refetch: refetchExecutions,
	} = useExecutionsWindow(chartWindow);

	// The window fetch is capped at one page (limit=1000). When the API
	// returns a continuation token the window is truncated — the cards and
	// chart annotate themselves and the chart clamps its time domain.
	const windowExecutions = executionsData?.executions;
	const windowTruncated = Boolean(executionsData?.continuation_token);
	const outcomes = useMemo(
		() => summarizeOutcomes(windowExecutions ?? []),
		[windowExecutions],
	);

	const {
		data: agentsData,
		isLoading: agentsLoading,
		refetch: refetchAgents,
	} = $api.useQuery("get", "/api/agents", {}, { staleTime: 60000 });
	const {
		data: appsData,
		isLoading: appsLoading,
		refetch: refetchApps,
	} = $api.useQuery("get", "/api/applications", {}, { staleTime: 60000 });

	const handleRefresh = () => {
		refetchDashboard();
		refetchExecutions();
		refetchAgents();
		refetchApps();
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

			{/* Headline numbers paired with the chart, inventory, and value */}
			<DashboardStatCards
				windowLabel={WINDOW_LABELS[chartWindow]}
				outcomes={outcomes}
				truncated={windowTruncated}
				executionsLoading={executionsLoading}
				executionsError={executionsError}
				inventory={{
					workflows: metrics?.workflow_count ?? 0,
					forms: metrics?.form_count ?? 0,
					agents: agentsData?.length ?? 0,
					apps: appsData?.total ?? 0,
				}}
				inventoryLoading={isLoading || agentsLoading || appsLoading}
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
			/>

			{/* Executions over time — successes and failures overlaid */}
			<ExecutionsOverTimeCard
				window={chartWindow}
				onWindowChange={setChartWindow}
				executions={windowExecutions}
				outcomes={outcomes}
				truncated={windowTruncated}
				isLoading={executionsLoading}
				isError={executionsError}
			/>
		</div>
	);
}
