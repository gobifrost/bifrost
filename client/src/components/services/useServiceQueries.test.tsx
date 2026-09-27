import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
	serviceLogDateFilters,
	serviceLogsQueryKey,
	useServiceLogs,
} from "./useServiceQueries";

describe("serviceLogDateFilters", () => {
	it("maps an empty window to unbounded filters", () => {
		expect(serviceLogDateFilters(undefined)).toEqual({
			startDate: undefined,
			endDate: undefined,
		});
	});

	it("maps day bounds under the panel predicate", () => {
		const day = new Date("2026-09-20T07:12:44Z");
		const filters = serviceLogDateFilters({ from: day, to: day });
		const start = new Date(filters.startDate!);
		const end = new Date(filters.endDate!);
		// Day granularity in local time (same predicate as the panel).
		expect([
			start.getHours(),
			start.getMinutes(),
			start.getSeconds(),
		]).toEqual([0, 0, 0]);
		expect([
			end.getHours(),
			end.getMinutes(),
			end.getSeconds(),
		]).toEqual([23, 59, 59]);
		// The filtered instant sits inside [start, end].
		expect(start.getTime()).toBeLessThanOrEqual(day.getTime());
		expect(end.getTime()).toBeGreaterThanOrEqual(day.getTime());
	});
});

describe("serviceLogsQueryKey", () => {
	it("scopes log queries under the service detail key", () => {
		const key = serviceLogsQueryKey("svc-1", { limit: 200 });
		expect(key[0]).toBe("service");
		expect(key[1]).toBe("svc-1");
		expect(key[2]).toBe("logs");
	});
});

describe("useServiceLogs", () => {
	const listServiceLogs = vi.hoisted(() => vi.fn());
	vi.mock("@/services/services", async (importOriginal) => ({
		...((await importOriginal()) as object),
		listServiceLogs: (...args: unknown[]) =>
			listServiceLogs(...args),
	}));

	function wrapper({ children }: { children: ReactNode }) {
		return (
			<QueryClientProvider
				client={
					new QueryClient({
						defaultOptions: {
							queries: { retry: false },
						},
					})
				}
			>
				{children}
			</QueryClientProvider>
		);
	}

	it("accumulates newest-first pages by continuation token", async () => {
		listServiceLogs.mockImplementation(
			(_id: unknown, filters: { continuationToken?: string }) =>
				Promise.resolve(
					filters.continuationToken === "tok-1"
						? { items: [{ id: 2 }], total: 2, continuation_token: null }
						: {
								items: [{ id: 1 }],
								total: 2,
								continuation_token: "tok-1",
							},
				),
		);
		const { result } = renderHook(
			() => useServiceLogs("svc-1", {}),
			{ wrapper },
		);
		await waitFor(() =>
			expect(result.current.data?.pages).toHaveLength(1),
		);
		expect(listServiceLogs).toHaveBeenCalledWith(
			"svc-1",
			expect.objectContaining({
				continuationToken: undefined,
				order: "newest_first",
			}),
		);
		await result.current.fetchNextPage();
		await waitFor(() =>
			expect(result.current.data?.pages).toHaveLength(2),
		);
		expect(listServiceLogs).toHaveBeenCalledWith(
			"svc-1",
			expect.objectContaining({ continuationToken: "tok-1" }),
		);
		expect(
			result.current.data?.pages.flatMap((p) => p.items),
		).toHaveLength(2);
		expect(result.current.hasNextPage).toBe(false);
	});
});
