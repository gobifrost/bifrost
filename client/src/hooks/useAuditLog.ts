import { $api } from "@/lib/api-client";
import { sameQueryParamsExcept } from "@/lib/paginated-query";
import type { components, paths } from "@/lib/v1";

export type AuditLogEntry = components["schemas"]["AuditLogEntry"];
export type AuditLogListResponse =
	components["schemas"]["AuditLogListResponse"];
export type AuditLogGroup = components["schemas"]["AuditLogGroup"];
export type AuditGroupBy = NonNullable<
	NonNullable<
		NonNullable<
			paths["/api/audit"]["get"]["parameters"]["query"]
		>["group_by"]
	>
>;

export interface GetAuditLogParams {
	action?: string;
	resource_type?: string;
	outcome?: string;
	user_id?: string;
	workflow_id?: string;
	organization_id?: string;
	start_date?: string;
	end_date?: string;
	search?: string;
	limit?: number;
	continuation_token?: string;
}

export function useAuditLog(
	params: GetAuditLogParams = {},
	enabled = true,
	options: { preservePageData?: boolean } = {},
) {
	return $api.useQuery(
		"get",
		"/api/audit",
		{
			params: {
				query: params as Record<string, string | number | undefined>,
			},
		},
		{
			enabled,
			placeholderData: options.preservePageData
				? (previousData, previousQuery) =>
						sameQueryParamsExcept({ ...params }, previousQuery, [
							"continuation_token",
						])
							? previousData
							: undefined
				: undefined,
		},
	);
}

/** The filtered audit log grouped by `groupBy`: counts, newest time and a sample per group. */
export function useAuditGroups(
	groupBy: AuditGroupBy,
	filters: Omit<GetAuditLogParams, "limit" | "continuation_token">,
) {
	return $api.useQuery("get", "/api/audit", {
		params: {
			query: { ...filters, group_by: groupBy },
		},
	});
}

/** A stored access check as decided then and judged again now, once `enabled`. */
export function useAuditExplain(eventId: string, enabled: boolean) {
	return $api.useQuery(
		"get",
		"/api/audit/{event_id}/explain",
		{ params: { path: { event_id: eventId } } },
		// An archived event is a 404; asking again won't find it.
		{ enabled, retry: false },
	);
}
