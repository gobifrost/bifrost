import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { authFetch } from "@/lib/api-client";
import { useWorkerMetrics, type WorkerMetricsResponse } from "./workers";

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));

it("retains exact time buckets and unknown memory values from the metrics endpoint", async () => {
	const payload: WorkerMetricsResponse = {
		range: "7d",
		points: [
			{
				group: "Nov 10",
				bucket_start: "2026-11-10T01:30:00Z",
				worker_id: "a",
				memory_current: -1,
				memory_max: 4096,
				fork_count: 0,
				busy_count: 0,
				idle_count: 0,
			},
		],
	};
	vi.mocked(authFetch).mockResolvedValue(
		new Response(JSON.stringify(payload)),
	);
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	const wrapper = ({ children }: { children: ReactNode }) =>
		createElement(QueryClientProvider, { client }, children);
	const { result, unmount } = renderHook(() => useWorkerMetrics("7d"), {
		wrapper,
	});
	await waitFor(() => expect(result.current.isSuccess).toBe(true));
	expect(authFetch).toHaveBeenCalledWith(
		"/api/platform/workers/metrics?range=7d",
	);
	expect(result.current.data).toEqual(payload);
	unmount();
	client.clear();
});
