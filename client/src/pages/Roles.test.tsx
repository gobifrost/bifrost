import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseRolesPage = vi.fn();
const mockDeleteMutate = vi.fn();
const mockUseMediaQuery = vi.fn();

vi.mock("@/hooks/useRoles", () => ({
	useRolesPage: (...args: unknown[]) => mockUseRolesPage(...args),
	useDeleteRole: () => ({
		mutate: mockDeleteMutate,
		isPending: false,
		reset: vi.fn(),
		isError: false,
	}),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: (...args: unknown[]) => mockUseMediaQuery(...args),
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

const longRole = {
	...role,
	id: "role-2",
	name: "Enterprise security and finance operations administrators with extremely long names",
	description:
		"Oversees access, billing, and policy controls across large deployments",
};

describe("Roles", () => {
	beforeEach(() => {
		mockUseRolesPage.mockReset();
		mockDeleteMutate.mockReset();
		mockUseMediaQuery.mockReturnValue(false);
	});

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
		expect(screen.getByText(/1.25 of 30/)).toBeInTheDocument();
		expect(
			screen.getByRole("columnheader", { name: "Name" }),
		).toHaveAttribute("aria-sort", "ascending");
		expect(
			screen.getByRole("button", { name: "Name" }),
		).toBeInTheDocument();
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

		await user.click(screen.getByRole("button", { name: "Next" }));
		await waitFor(() => {
			expect(mockUseRolesPage).toHaveBeenLastCalledWith(
				expect.objectContaining({ limit: 25, offset: 25 }),
			);
		});
	});

	it("opens a role row href on ctrl-click from a plain cell", () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		mockUseRolesPage.mockReturnValue({
			data: { items: [role], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});

		renderWithProviders(<Roles />);

		fireEvent.click(screen.getByText("Manage billing"), { ctrlKey: true });

		expect(open).toHaveBeenCalledWith("/roles/role-1", "_blank");
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

	it("retains cached roles during a failed refresh and retries", async () => {
		const refetch = vi.fn();
		mockUseRolesPage.mockReturnValue({
			data: { items: [role], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: true,
			refetch,
		});
		const { user } = renderWithProviders(<Roles />);
		expect(screen.getByText("Billing admins")).toBeVisible();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Previously loaded records",
		);
		await user.click(screen.getByRole("button", { name: "Retry loading" }));
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("keeps deletion confirmation open until the mutation succeeds", async () => {
		mockUseRolesPage.mockReturnValue({
			data: { items: [role], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(<Roles />);
		await user.click(
			screen.getByRole("button", { name: "Billing admins actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(screen.getByRole("button", { name: "Delete role" }));
		expect(mockDeleteMutate).toHaveBeenCalledWith(
			{ params: { path: { role_id: "role-1" } } },
			expect.objectContaining({ onSuccess: expect.any(Function) }),
		);
		expect(screen.getByRole("alertdialog")).toBeVisible();
	});

	it("renders a mobile record list below 1024px with 44px controls", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseRolesPage.mockReturnValue({
			data: { items: [longRole], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});

		renderWithProviders(<Roles />);

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(
			screen.getByRole("link", {
				name: /enterprise security and finance operations administrators/i,
			}),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /2 users/i })).toHaveClass(
			"h-11",
		);
		expect(
			screen.getByRole("button", {
				name: /enterprise security and finance operations administrators.*actions/i,
			}),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: /sort descending/i }),
		).toHaveClass("h-11", "w-11");
		expect(
			screen
				.getByRole("link", {
					name: /enterprise security and finance operations administrators/i,
				})
				.getAttribute("class"),
		).not.toContain("truncate");
		expect(
			screen
				.getByRole("link", { name: /2 users/i })
				.getAttribute("class"),
		).toContain("rounded-[var(--bf-radius-control)]");
	});
});
