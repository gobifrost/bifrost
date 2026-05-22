/**
 * Usage Reports API service using openapi-react-query pattern
 *
 * Provides hooks for fetching AI usage and resource consumption data.
 */

import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type UsageReportResponse = components["schemas"]["UsageReportResponse"];
export type UsageReportSummary = components["schemas"]["UsageReportSummary"];
export type UsageTrend = components["schemas"]["UsageTrend"];
export type WorkflowUsage = components["schemas"]["WorkflowUsage"];
export type ConversationUsage = components["schemas"]["ConversationUsage"];
export type OrganizationUsage = components["schemas"]["OrganizationUsage"];
export type KnowledgeStorageUsage =
	components["schemas"]["KnowledgeStorageUsage"];
export type KnowledgeStorageTrend =
	components["schemas"]["KnowledgeStorageTrend"];

export type UsageSource = "executions" | "chat" | "agents" | "all";

/**
 * Hook to fetch usage report for a date range.
 *
 * @param startDate - Start date in YYYY-MM-DD format
 * @param endDate - End date in YYYY-MM-DD format
 * @param source - Filter by source: executions, chat, or all
 * @param orgId - Optional organization ID filter (undefined = all, null = global, string = specific org)
 */
export function useUsageReport(
	startDate: string,
	endDate: string,
	source: UsageSource = "all",
	orgId?: string | null,
) {
	return $api.useQuery(
		"get",
		"/api/reports/usage",
		{
			params: {
				query: {
					start_date: startDate,
					end_date: endDate,
					source,
					// Only pass org_id if it's a specific org (string), not undefined or null
					...(typeof orgId === "string" ? { org_id: orgId } : {}),
				},
			},
		},
		{
			enabled: !!startDate && !!endDate,
		},
	);
}
