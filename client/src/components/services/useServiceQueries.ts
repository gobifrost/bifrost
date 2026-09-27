import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { endOfDay, startOfDay } from "date-fns";
import type { DateRange } from "react-day-picker";

import {
	getService,
	listServiceAttempts,
	listServiceLogs,
	listServices,
	SERVICES_QUERY_KEY,
	serviceDetailQueryKey,
	type ServiceLogFilters,
} from "@/services/services";
import type {
	ServiceAttemptList,
	ServiceList,
	ServiceLogList,
} from "@/services/services";
import type { components } from "@/lib/v1";

export type Service = components["schemas"]["ServiceResponse"];
export type ServiceAttempt =
	components["schemas"]["ServiceAttemptResponse"];

/**
 * Live services queries (2.2).
 *
 * Keys match the invalidation targets in `useServiceAction`, so control
 * actions refresh these queries. Refetch-on-focus comes from the global
 * QueryClient defaults; pages add explicit Refresh buttons on top.
 */

export function serviceAttemptsQueryKey(serviceId: string): string[] {
	return [...serviceDetailQueryKey(serviceId), "attempts"];
}

export function useServicesList(options?: { enabled?: boolean }) {
	return useQuery<ServiceList>({
		queryKey: [...SERVICES_QUERY_KEY],
		queryFn: () => listServices(),
		enabled: options?.enabled ?? true,
	});
}

export function useServiceDetail(serviceId: string | undefined) {
	return useQuery<Service>({
		queryKey: serviceId ? serviceDetailQueryKey(serviceId) : ["service"],
		queryFn: () => getService(serviceId as string),
		enabled: Boolean(serviceId),
	});
}

export function useServiceAttempts(serviceId: string | undefined) {
	return useQuery<ServiceAttemptList>({
		queryKey: serviceId
			? serviceAttemptsQueryKey(serviceId)
			: ["service", "attempts"],
		queryFn: () => listServiceAttempts(serviceId as string),
		enabled: Boolean(serviceId),
	});
}

export function serviceLogsQueryKey(
	serviceId: string,
	filters: ServiceLogFilters,
): string[] {
	// Paging stays out of the key: pages accumulate under one entry.
	const { attemptId, levels, startDate, endDate } = filters;
	return [
		...serviceDetailQueryKey(serviceId),
		"logs",
		JSON.stringify({ attemptId, levels, startDate, endDate }),
	];
}

/**
 * Map the panel date window to log-query bounds under the same predicate
 * the panel applies (day granularity): server-side and client-side
 * filtering agree, so the visible timeline stays exact while pages converge.
 */
export function serviceLogDateFilters(
	dateRange: DateRange | undefined,
): ServiceLogFilters {
	return {
		startDate: dateRange?.from
			? startOfDay(dateRange.from).toISOString()
			: undefined,
		endDate: dateRange?.to
			? endOfDay(dateRange.to).toISOString()
			: undefined,
	};
}

/** One logs page (newest 200 lines first). */
export const SERVICE_LOGS_PAGE_SIZE = 200;

export function useServiceLogs(
	serviceId: string | undefined,
	filters: ServiceLogFilters,
) {
	const { attemptId, levels, startDate, endDate } = filters;
	return useInfiniteQuery<ServiceLogList>({
		queryKey: serviceId
			? serviceLogsQueryKey(serviceId, filters)
			: ["service", "logs"],
		queryFn: ({ pageParam }) =>
			listServiceLogs(serviceId as string, {
				attemptId,
				levels,
				startDate,
				endDate,
				limit: SERVICE_LOGS_PAGE_SIZE,
				continuationToken: (pageParam as string | null) ?? undefined,
				order: "newest_first",
			}),
		initialPageParam: null as string | null,
		getNextPageParam: (lastPage) =>
			lastPage.continuation_token ?? undefined,
		enabled: Boolean(serviceId),
	});
}
