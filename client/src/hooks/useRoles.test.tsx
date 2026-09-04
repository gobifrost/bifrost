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

import { useAssignUsersToRole, useRoleUsersPage } from "./useRoles";

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
	return (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
}

describe("useRoleUsersPage", () => {
	beforeEach(() => {
		mockGet.mockReset();
		mockUseMutation.mockReset();
		queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false } },
		});
	});

	it("requests a bounded, searchable assigned-user page", async () => {
		mockGet.mockResolvedValue({
			data: { user_ids: ["user-1"], users: [], total: 31 },
			error: undefined,
		});

		const { result } = renderHook(
			() =>
				useRoleUsersPage("role-1", {
					search: "  alice  ",
					limit: 25,
					offset: 25,
				}),
			{ wrapper },
		);

		await waitFor(() => expect(result.current.isSuccess).toBe(true));
		expect(result.current.data?.total).toBe(31);
		expect(mockGet).toHaveBeenCalledWith("/api/roles/{role_id}/users", {
			params: {
				path: { role_id: "role-1" },
				query: { search: "alice", limit: 25, offset: 25 },
			},
		});
	});

	it("invalidates every assigned-user page after assignment", () => {
		const invalidate = vi.spyOn(queryClient, "invalidateQueries");
		mockUseMutation.mockReturnValue({ mutate: vi.fn() });

		renderHook(() => useAssignUsersToRole(), { wrapper });
		const options = mockUseMutation.mock.calls[0][2];
		options.onSuccess(undefined, {
			params: { path: { role_id: "role-1" } },
			body: { user_ids: ["user-1"] },
		});

		expect(invalidate).toHaveBeenCalledWith({
			queryKey: ["get", "/api/roles/{role_id}/users"],
		});
	});
});
