import type { ReactNode } from "react";
import { createElement } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { authFetchMock, useQueryMock } = vi.hoisted(() => ({
	authFetchMock: vi.fn(),
	useQueryMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
	$api: { useQuery: (...args: unknown[]) => useQueryMock(...args) },
}));

import { ApiError } from "@/lib/api-error";
import {
	USER_ACCESS_QUERY_KEY,
	usePermissionCatalog,
	useUserAccessMap,
} from "./access";

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
	return createElement(
		QueryClientProvider,
		{ client: queryClient },
		children,
	);
}

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status });

describe("access service", () => {
	beforeEach(() => {
		authFetchMock.mockReset();
		useQueryMock.mockReset();
		queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
	});

	it("reads the permission catalog from its endpoint", () => {
		usePermissionCatalog();

		expect(useQueryMock).toHaveBeenCalledWith(
			"get",
			"/api/permissions/catalog",
		);
	});

	it("loads a person's access map", async () => {
		const map = { user_id: "user-1", rows: [], reach: [] };
		authFetchMock.mockResolvedValue(json(map));

		const { result } = renderHook(() => useUserAccessMap("user-1"), {
			wrapper,
		});

		await waitFor(() => expect(result.current.isSuccess).toBe(true));
		expect(result.current.data).toEqual(map);
		expect(authFetchMock).toHaveBeenCalledWith("/api/users/user-1/access");
	});

	it("keys the map under the invalidation prefix", async () => {
		authFetchMock.mockResolvedValue(json({ rows: [] }));

		const { result } = renderHook(() => useUserAccessMap("user-1"), {
			wrapper,
		});
		await waitFor(() => expect(result.current.isSuccess).toBe(true));

		const cached = queryClient.getQueryCache().findAll({
			queryKey: [...USER_ACCESS_QUERY_KEY],
		});
		expect(cached).toHaveLength(1);
	});

	it("does not request without a user", () => {
		renderHook(() => useUserAccessMap(undefined), { wrapper });

		expect(authFetchMock).not.toHaveBeenCalled();
	});

	it("surfaces the server's reason when the request is refused", async () => {
		authFetchMock.mockResolvedValue(
			json({ detail: "You can't view this person's access" }, 403),
		);

		const { result } = renderHook(() => useUserAccessMap("user-1"), {
			wrapper,
		});

		await waitFor(() => expect(result.current.isError).toBe(true));
		expect(result.current.error).toBeInstanceOf(ApiError);
		expect((result.current.error as ApiError).statusCode).toBe(403);
	});
});
