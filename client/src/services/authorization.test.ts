import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";

import { makeQueryClient } from "@/test-utils";
import {
	AUTHORIZATION_QUERY_KEY,
	invalidateAuthorization,
} from "@/lib/authorization";

const mockGet = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: (...args: unknown[]) => mockGet(...args) },
}));

const auth = {
	user: { id: "user-1" } as { id: string } | null,
	isAuthenticated: true,
	hasRole: vi.fn(() => false),
};
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));

import { fetchAuthorization, useAuthorization } from "./authorization";

const operatorSummary = {
	is_platform_admin: false,
	home_organization_id: "00000000-0000-0000-0000-000000000002",
	provider_organization_id: "00000000-0000-0000-0000-000000000002",
	base_role: { id: "r", name: "User" },
	grants: [
		{
			permission: "users.read",
			boundary: { kind: "managed_organizations", organization_id: null },
		},
	],
};

function wrapperFor(client = makeQueryClient()) {
	return {
		client,
		wrapper: ({ children }: { children: ReactNode }) =>
			createElement(QueryClientProvider, { client }, children),
	};
}

beforeEach(() => {
	mockGet.mockReset();
	auth.user = { id: "user-1" };
	auth.isAuthenticated = true;
	auth.hasRole.mockReturnValue(false);
});

describe("fetchAuthorization", () => {
	it("reads the signed-in user's summary", async () => {
		mockGet.mockResolvedValue({ data: operatorSummary });

		await expect(fetchAuthorization()).resolves.toEqual(operatorSummary);
		expect(mockGet).toHaveBeenCalledWith("/api/auth/authorization");
	});

	it("throws the server error", async () => {
		mockGet.mockResolvedValue({ error: { detail: "nope" } });

		await expect(fetchAuthorization()).rejects.toEqual({ detail: "nope" });
	});
});

describe("useAuthorization", () => {
	it("keys the summary by user and answers permission questions", async () => {
		mockGet.mockResolvedValue({ data: operatorSummary });
		const { client, wrapper } = wrapperFor();

		const { result } = renderHook(() => useAuthorization(), { wrapper });

		await waitFor(() => expect(result.current.authorization).toBeDefined());
		expect(
			client.getQueryData([...AUTHORIZATION_QUERY_KEY, "user-1"]),
		).toEqual(operatorSummary);
		expect(result.current.canAnywhere("users.read")).toBe(true);
		expect(
			result.current.canAt("users.read", {
				kind: "org",
				id: "00000000-0000-0000-0000-000000000002",
			}),
		).toBe(false);
		expect(result.current.isPlatformAdmin).toBe(false);
	});

	it("does not ask for embed sessions or signed-out users", () => {
		auth.hasRole.mockReturnValue(true);
		const { wrapper } = wrapperFor();

		const { result } = renderHook(() => useAuthorization(), { wrapper });

		expect(result.current.isLoading).toBe(false);
		expect(result.current.canAnywhere("users.read")).toBe(false);
		expect(mockGet).not.toHaveBeenCalled();
	});

	it("refetches after invalidation", async () => {
		mockGet.mockResolvedValue({ data: operatorSummary });
		const { client, wrapper } = wrapperFor();
		renderHook(() => useAuthorization(), { wrapper });
		await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));

		await invalidateAuthorization(client);

		expect(mockGet).toHaveBeenCalledTimes(2);
	});
});
