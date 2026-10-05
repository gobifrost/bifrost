import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockGet, mockUseMutation } = vi.hoisted(() => ({
	mockGet: vi.fn(),
	mockUseMutation: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: (...args: unknown[]) => mockGet(...args) },
	$api: { useQuery: vi.fn(), useMutation: mockUseMutation },
}));

import {
	useReplaceUserRoleAssignments,
	useUpdateUser,
	useUsersPage,
} from "./useUsers";

const queryClient = new QueryClient({
	defaultOptions: { queries: { retry: false } },
});

function wrapper({ children }: { children: ReactNode }) {
	return (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
}

describe("useUsersPage", () => {
	beforeEach(() => mockGet.mockReset());

	it("returns the server total for a bounded user page", async () => {
		mockGet.mockResolvedValue({
			data: [{ id: "user-1" }],
			error: undefined,
			response: new Response(null, {
				headers: { "X-Total-Count": "10000" },
			}),
		});

		const { result } = renderHook(
			() =>
				useUsersPage({
					search: "  alice  ",
					sortBy: "name",
					sortDirection: "asc",
					limit: 25,
					offset: 50,
				}),
			{ wrapper },
		);

		await waitFor(() => expect(result.current.isSuccess).toBe(true));
		expect(result.current.data).toEqual({
			items: [{ id: "user-1" }],
			total: 10000,
		});
		expect(mockGet).toHaveBeenCalledWith("/api/users", {
			params: {
				query: {
					scope: undefined,
					include_inactive: undefined,
					search: "alice",
					sort_by: "name",
					sort_direction: "asc",
					limit: 25,
					offset: 50,
				},
			},
		});
	});
});

describe("access map invalidation", () => {
	const accessKey = { queryKey: ["get", "/api/users/{user_id}/access"] };

	beforeEach(() => {
		vi.restoreAllMocks();
		mockUseMutation.mockReset();
		mockUseMutation.mockReturnValue({ mutate: vi.fn() });
	});

	it("refreshes access maps after role assignments are replaced", () => {
		const invalidate = vi.spyOn(queryClient, "invalidateQueries");

		renderHook(() => useReplaceUserRoleAssignments(), { wrapper });
		const options = mockUseMutation.mock.calls[0][2];
		options.onSuccess(
			{ base_role: null },
			{ params: { path: { user_id: "user-1" } } },
		);

		expect(invalidate).toHaveBeenCalledWith(accessKey);
	});

	it("refreshes access maps after a person is moved or edited", () => {
		const invalidate = vi.spyOn(queryClient, "invalidateQueries");

		renderHook(() => useUpdateUser(), { wrapper });
		mockUseMutation.mock.calls[0][2].onSuccess();

		expect(invalidate).toHaveBeenCalledWith(accessKey);
	});
});
