import type { PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockListBuilderTargets = vi.fn();
const mockListBuilderSolutions = vi.fn();

vi.mock("@/services/builder", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/builder")>(
			"@/services/builder",
		);
	return {
		...actual,
		listBuilderTargets: (...args: unknown[]) => mockListBuilderTargets(...args),
		listBuilderSolutions: (...args: unknown[]) =>
			mockListBuilderSolutions(...args),
	};
});

import { useBuilderAccess } from "./useBuilderAccess";

function wrapper({ children }: PropsWithChildren) {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
	vi.clearAllMocks();
	mockListBuilderTargets.mockResolvedValue({
		organizations: [
			{
				id: "org-1",
				name: "Customer",
				is_provider: false,
				can_read: true,
				can_execute: true,
				can_build_resources: true,
			},
		],
		can_view_all: true,
		can_open_global_workspace: false,
		ai_configured: true,
		builder_ready: true,
		builder_blockers: [],
		is_platform_admin: false,
	});
	mockListBuilderSolutions.mockResolvedValue({
		solutions: [{ id: "solution-1" }],
		total: 1,
	});
});

describe("useBuilderAccess", () => {
	it("discovers selectable boundaries before loading the build library", async () => {
		const { result } = renderHook(() => useBuilderAccess(), { wrapper });

		await waitFor(() => expect(result.current.isLoading).toBe(false));
		await waitFor(() => expect(result.current.solutions).toHaveLength(1));

		expect(result.current.canBuild).toBe(true);
		expect(result.current.canAccessBuilder).toBe(true);
		expect(result.current.organizationTargets).toHaveLength(1);
		expect(result.current.organizationTargets[0].can_build_resources).toBe(true);
		expect(result.current.canViewAll).toBe(true);
		expect(mockListBuilderTargets).toHaveBeenCalled();
		expect(mockListBuilderSolutions).toHaveBeenCalled();
	});

	it("keeps support-only operators in Builder without granting build execution", async () => {
		mockListBuilderTargets.mockResolvedValue({
			organizations: [
				{
					id: "org-1",
					name: "Customer",
					is_provider: false,
					can_read: true,
					can_execute: false,
					can_build_resources: false,
				},
			],
			can_view_all: true,
			can_open_global_workspace: false,
			ai_configured: true,
			builder_ready: true,
			builder_blockers: [],
			is_platform_admin: false,
		});

		const { result } = renderHook(() => useBuilderAccess(), { wrapper });

		await waitFor(() => expect(result.current.isLoading).toBe(false));
		expect(result.current.canAccessBuilder).toBe(true);
		expect(result.current.hasPermission).toBe(true);
		expect(result.current.canBuild).toBe(false);
		expect(result.current.canViewAll).toBe(true);
	});
});
