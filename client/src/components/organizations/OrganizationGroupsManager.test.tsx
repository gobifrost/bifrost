import { describe, expect, it, beforeEach, vi } from "vitest";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const mockUseQuery = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();
const mockRefetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => mockUseQuery(...args),
		useMutation: (method: string, path: string) => {
			if (method === "post" && path === "/api/organization-groups") {
				return { mutateAsync: mockCreate, isPending: false };
			}
			if (method === "patch" && path === "/api/organization-groups/{group_id}") {
				return { mutateAsync: mockUpdate, isPending: false };
			}
			if (method === "delete" && path === "/api/organization-groups/{group_id}") {
				return { mutateAsync: mockDelete, isPending: false };
			}
			throw new Error(`Unexpected mutation ${method} ${path}`);
		},
	},
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

import { OrganizationGroupsManager } from "./OrganizationGroupsManager";

const organizations = [
	{ id: "org-provider", name: "Provider", is_provider: true, domain: null },
	{ id: "org-1", name: "Acme Corp", is_provider: false, domain: "acme.com" },
	{ id: "org-2", name: "Bravo Labs", is_provider: false, domain: "bravo.io" },
];

const groups = [
	{
		id: "group-1",
		name: "Managed Customers",
		owner_organization_id: "org-provider",
		member_organization_ids: ["org-1"],
		created_at: "2026-08-19T00:00:00Z",
		updated_at: "2026-08-19T00:00:00Z",
	},
];

beforeEach(() => {
	mockUseQuery.mockReturnValue({
		data: groups,
		isLoading: false,
		refetch: mockRefetch,
	});
	mockCreate.mockReset();
	mockUpdate.mockReset();
	mockDelete.mockReset();
	mockRefetch.mockReset();
});

describe("OrganizationGroupsManager", () => {
	it("lists groups and opens the editor for the selected row", async () => {
		const { user } = renderWithProviders(
			<OrganizationGroupsManager organizations={organizations as never} />,
		);

		expect(screen.getByText("Managed Customers")).toBeInTheDocument();
		expect(screen.getByText("Provider", { selector: "td" })).toBeInTheDocument();
		expect(screen.getByText("Acme Corp")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /managed customers actions/i }));
		await user.click(screen.getByText("Edit"));

		const dialog = screen.getByRole("dialog", { name: /edit organization group/i });
		expect(within(dialog).getByDisplayValue("Managed Customers")).toBeInTheDocument();
		expect(within(dialog).getByText("Provider")).toBeInTheDocument();
	});

	it("creates a provider-owned group with selected member organizations", async () => {
		mockUseQuery.mockReturnValue({
			data: [],
			isLoading: false,
			refetch: mockRefetch,
		});
		mockCreate.mockResolvedValueOnce({});

		const { user } = renderWithProviders(
			<OrganizationGroupsManager organizations={organizations as never} />,
		);

		await user.click(screen.getAllByRole("button", { name: /new group/i })[0]!);
		const dialog = screen.getByRole("dialog", { name: /create organization group/i });
		await user.type(within(dialog).getByLabelText(/group name/i), "North Team");

		await user.click(within(dialog).getByRole("combobox", { name: /member organizations/i }));
		await user.click(screen.getByText("Acme Corp"));
		await user.click(screen.getByText("Bravo Labs"));

		await user.click(within(dialog).getByRole("button", { name: /create group/i }));

		await waitFor(() => expect(mockCreate).toHaveBeenCalled());
		expect(mockCreate.mock.calls[0]![0]).toEqual({
			body: {
				name: "North Team",
				member_organization_ids: ["org-1", "org-2"],
			},
		});
	});

	it("updates and deletes groups", async () => {
		mockUpdate.mockResolvedValueOnce({});
		mockDelete.mockResolvedValueOnce({});
		const { user } = renderWithProviders(
			<OrganizationGroupsManager organizations={organizations as never} />,
		);

		await user.click(screen.getByText("Managed Customers"));
		const dialog = screen.getByRole("dialog", { name: /edit organization group/i });
		await user.clear(within(dialog).getByLabelText(/group name/i));
		await user.type(within(dialog).getByLabelText(/group name/i), "Premier Customers");
		await user.click(within(dialog).getByRole("combobox", { name: /member organizations/i }));
		await user.click(screen.getByText("Bravo Labs"));
		await user.click(within(dialog).getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
		expect(mockUpdate.mock.calls[0]![0]).toEqual({
			params: { path: { group_id: "group-1" } },
			body: {
				name: "Premier Customers",
				member_organization_ids: ["org-1", "org-2"],
			},
		});

		await user.click(screen.getByRole("button", { name: /managed customers actions/i }));
		await user.click(screen.getByText("Delete"));
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() => expect(mockDelete).toHaveBeenCalled());
		expect(mockDelete.mock.calls[0]![0]).toEqual({
			params: { path: { group_id: "group-1" } },
		});
	});
});
