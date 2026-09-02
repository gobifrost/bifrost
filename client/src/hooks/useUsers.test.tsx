import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: (...args: unknown[]) => mockGet(...args) },
	$api: { useQuery: vi.fn(), useMutation: vi.fn() },
}));

import { useUsersPage } from "./useUsers";

function wrapper({ children }: { children: ReactNode }) {
	return (
		<QueryClientProvider
			client={
				new QueryClient({
					defaultOptions: { queries: { retry: false } },
				})
			}
		>
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
