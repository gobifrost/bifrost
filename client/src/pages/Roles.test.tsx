import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseRolesPage = vi.fn();
const mockDeleteMutate = vi.fn();

vi.mock("@/hooks/useRoles", () => ({
	useRolesPage: (...args: unknown[]) => mockUseRolesPage(...args),
	useDeleteRole: () => ({ mutate: mockDeleteMutate, isPending: false }),
}));

vi.mock("@/components/roles/RoleDialog", () => ({
	RoleDialog: () => null,
}));

import { Roles } from "./Roles";

const role = {
	id: "role-1",
	name: "Billing admins",
	description: "Manage billing",
	permissions: {},
	created_by: "admin@example.com",
	created_at: "2026-01-01T00:00:00Z",
	updated_at: "2026-01-01T00:00:00Z",
	consumer_counts: {
		users: 2,
		forms: 0,
		agents: 0,
		apps: 0,
		workflows: 0,
		knowledge: 0,
	},
};

describe("Roles", () => {
	it("requests a bounded page and navigates to the next page", async () => {
		mockUseRolesPage.mockReturnValue({
			data: { items: [role], total: 30 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(<Roles />);

		expect(screen.getByText("Billing admins")).toBeInTheDocument();
		expect(screen.getByText("1–25 of 30")).toBeInTheDocument();
		expect(
			screen
				.getByRole("navigation", { name: /pagination/i })
				.closest("tfoot"),
		).not.toBeNull();
		expect(
			screen.getAllByRole("table")[0].parentElement?.parentElement,
		).toHaveClass("max-h-full");
		expect(mockUseRolesPage).toHaveBeenLastCalledWith(
			expect.objectContaining({ limit: 25, offset: 0 }),
		);

		await user.click(screen.getByRole("link", { name: /next page/i }));
		await waitFor(() => {
			expect(mockUseRolesPage).toHaveBeenLastCalledWith(
				expect.objectContaining({ limit: 25, offset: 25 }),
			);
		});
	});

	it("sends debounced search to the server and resets the page", async () => {
		mockUseRolesPage.mockReturnValue({
			data: { items: [role], total: 30 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(<Roles />);

		await user.type(
			screen.getByPlaceholderText(/search roles by name or description/i),
			"billing",
		);
		await waitFor(() => {
			expect(mockUseRolesPage).toHaveBeenLastCalledWith(
				expect.objectContaining({ search: "billing", offset: 0 }),
			);
		});
	});
});
