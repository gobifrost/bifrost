import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getAuthorizationTargets = vi.fn();
const mockUser = {
	id: "user-one",
	organizationId: "provider-one",
};

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isAuthenticated: true, user: mockUser }),
}));
vi.mock("@/lib/auth-token", () => ({ isEmbedSession: () => false }));
vi.mock("@/services/authorizationTargets", () => ({
	getAuthorizationTargets: (...args: unknown[]) =>
		getAuthorizationTargets(...args),
}));

import {
	AuthorizationBoundaryProvider,
	useAuthorizationBoundary,
} from "./AuthorizationBoundaryContext";

const targets = [
	{
		boundary: "organization:customer-one",
		kind: "organization" as const,
		label: "Customer One",
		capabilities: ["builder.read"],
		organization_id: "customer-one",
		is_provider: false,
	},
	{
		boundary: "organization:provider-one",
		kind: "organization" as const,
		label: "Provider",
		capabilities: ["builder.read", "builder.execute"],
		organization_id: "provider-one",
		is_provider: true,
	},
	{
		boundary: "platform",
		kind: "platform" as const,
		label: "Global",
		capabilities: ["platform.superuser"],
		is_provider: false,
	},
];

function wrapper({ children }: { children: ReactNode }) {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return (
		<QueryClientProvider client={client}>
			<AuthorizationBoundaryProvider>
				{children}
			</AuthorizationBoundaryProvider>
		</QueryClientProvider>
	);
}

describe("AuthorizationBoundaryProvider", () => {
	beforeEach(() => {
		sessionStorage.clear();
		getAuthorizationTargets.mockReset();
		getAuthorizationTargets.mockResolvedValue({ targets });
	});

	it("defaults to the person's home organization instead of the broadest context", async () => {
		const { result } = renderHook(() => useAuthorizationBoundary(), {
			wrapper,
		});

		await waitFor(() =>
			expect(result.current.selectedBoundary).toBe(
				"organization:provider-one",
			),
		);
		expect(result.current.hasSelectedCapability("builder.execute")).toBe(
			true,
		);
	});

	it("persists an explicit context selection for the current session", async () => {
		const { result } = renderHook(() => useAuthorizationBoundary(), {
			wrapper,
		});
		await waitFor(() =>
			expect(result.current.selectedTarget).toBeDefined(),
		);

		act(() => result.current.setSelectedBoundary("platform"));

		expect(result.current.selectedBoundary).toBe("platform");
		expect(
			sessionStorage.getItem("bifrost-authorization-boundary:user-one"),
		).toBe("platform");
		expect(result.current.hasSelectedCapability("solutions.publish")).toBe(
			true,
		);
	});
});
