import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import createClient from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { paths } from "@/lib/v1";
import { makeQueryClient } from "@/test-utils";

const mockUseQuery = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: (...args: unknown[]) => mockUseQuery(...args) },
	apiClient: {},
}));

import { isNotFoundError, useExecution, useExecutions } from "./useExecutions";

// A real openapi-react-query client over a stubbed fetch, so the hook's own
// retry and polling options drive the request count.
const fetchMock = vi.fn<(request: Request) => Promise<Response>>();
const fetchBackedApi = createQueryClient(
	createClient<paths>({
		baseUrl: "http://localhost",
		fetch: (request: Request) => fetchMock(request),
	}),
);

describe("useExecutions", () => {
	beforeEach(() => {
		mockUseQuery.mockReset();
	});

	it("preserves previous page data only for same-scope, same-filter continuation changes", () => {
		const previousData = {
			executions: [{ execution_id: "page-one" }],
			continuation_token: "next",
		};
		const previousQuery = {
			queryKey: [
				"get",
				"/api/executions",
				{
					params: {
						query: {
							scope: "org-1",
							status: "Running",
							continuationToken: "first",
						},
					},
				},
			],
		};

		useExecutions("org-1", { status: "Running" }, "second", {
			preservePageData: true,
		});

		const options = mockUseQuery.mock.calls[0][3];
		expect(options.placeholderData(previousData, previousQuery)).toBe(
			previousData,
		);

		useExecutions("org-2", { status: "Running" }, "second", {
			preservePageData: true,
		});

		expect(
			mockUseQuery.mock.calls[1][3].placeholderData(
				previousData,
				previousQuery,
			),
		).toBeUndefined();

		useExecutions("org-1", { status: "Failed" }, "second", {
			preservePageData: true,
		});

		expect(
			mockUseQuery.mock.calls[2][3].placeholderData(
				previousData,
				previousQuery,
			),
		).toBeUndefined();
	});
});

describe("useExecution", () => {
	beforeEach(() => {
		mockUseQuery.mockReset();
		mockUseQuery.mockImplementation(fetchBackedApi.useQuery);
		fetchMock.mockReset();
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("stops requesting a removed run after its retries", async () => {
		fetchMock.mockImplementation(
			async () =>
				new Response(
					JSON.stringify({
						detail: "Execution x not found. Finished runs are removed after 30 days.",
					}),
					{
						status: 404,
						headers: { "Content-Type": "application/json" },
					},
				),
		);
		const queryClient = makeQueryClient();
		const wrapper = ({ children }: { children: ReactNode }) =>
			createElement(
				QueryClientProvider,
				{ client: queryClient },
				children,
			);

		const { result } = renderHook(() => useExecution("x"), { wrapper });
		await act(() => vi.advanceTimersByTimeAsync(60_000));
		const settled = fetchMock.mock.calls.length;
		await act(() => vi.advanceTimersByTimeAsync(60_000));

		expect(settled).toBeLessThanOrEqual(6);
		expect(fetchMock).toHaveBeenCalledTimes(settled);
		expect(isNotFoundError(result.current.error)).toBe(true);
	});
});

describe("isNotFoundError", () => {
	it("recognizes a not-found detail and a 404 message", () => {
		expect(
			isNotFoundError({
				detail: "Agent run x not found. Finished runs are removed after 30 days.",
			}),
		).toBe(true);
		expect(isNotFoundError(new Error("Request failed: 404"))).toBe(true);
	});

	it("rejects other errors", () => {
		expect(isNotFoundError({ detail: "Internal server error" })).toBe(
			false,
		);
		expect(isNotFoundError(new Error("Synthetic failure"))).toBe(false);
		expect(isNotFoundError(null)).toBe(false);
	});
});
