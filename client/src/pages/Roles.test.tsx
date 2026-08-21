import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mocks = vi.hoisted(() => ({
	useAuthorizationBoundary: vi.fn(),
	refetch: vi.fn(),
	deleteRole: vi.fn(),
}));

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => mocks.useAuthorizationBoundary(),
}));
vi.mock("@/hooks/useRoles", () => ({
	PLATFORM_BOUNDARY_HEADERS: { "X-Bifrost-Boundary": "platform" },
	useRoles: () => ({
		data: [
			{
				id: "role-one",
				name: "Customer Builder",
				description: "Build for assigned customers",
				is_builtin: false,
				assignable_to_resources: true,
				consumer_counts: {},
			},
		],
		isLoading: false,
		refetch: mocks.refetch,
	}),
	useDeleteRole: () => ({ mutate: mocks.deleteRole, isPending: false }),
}));
vi.mock("@/components/roles/RoleDialog", () => ({
	RoleDialog: () => null,
}));

import { Roles } from "./Roles";

describe("Roles authorization context", () => {
	beforeEach(() => vi.clearAllMocks());

	it("keeps global role-definition actions out of a managed context", () => {
		mocks.useAuthorizationBoundary.mockReturnValue({
			selectedTarget: { kind: "managed_organizations" },
			hasSelectedCapability: () => true,
		});

		renderWithProviders(<Roles />);

		expect(screen.getByTitle("Refresh")).toBeVisible();
		expect(screen.queryByTitle("Create Role")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit Customer Builder" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Delete Customer Builder" }),
		).not.toBeInTheDocument();
	});

	it("shows role-definition actions with Platform write authority", () => {
		mocks.useAuthorizationBoundary.mockReturnValue({
			selectedTarget: { kind: "platform" },
			hasSelectedCapability: (capability: string) =>
				capability === "roles.readwrite",
		});

		renderWithProviders(<Roles />);

		expect(screen.getByTitle("Create Role")).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Edit Customer Builder" }),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Delete Customer Builder" }),
		).toBeVisible();
	});
});
