import { describe, expect, it, vi } from "vitest";

import {
	fireEvent,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";
import type { PermissionCatalogEntry } from "@/services/access";

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

const authz = vi.hoisted(() => ({ canManage: true }));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ meets: () => authz.canManage }),
}));

function catalogEntry(
	domain: string,
	title: string,
	privileged: string[] = [],
): PermissionCatalogEntry {
	return {
		domain,
		title,
		area: "Automation",
		description: `${title}.`,
		who_should_hold: "Anyone.",
		actions: ["read", "readwrite"],
		privileged,
		scope: "per_organization",
		enforced: false,
	};
}

vi.mock("@/services/access", () => ({
	usePermissionCatalog: () => ({
		data: [
			catalogEntry("agents", "Agents"),
			catalogEntry("forms", "Forms"),
			catalogEntry("tables", "Tables"),
			catalogEntry("workflows", "Workflows"),
			catalogEntry("roles", "Roles", ["roles.readwrite"]),
		],
	}),
}));

import { Roles } from "./Roles";

const role = {
	id: "role-1",
	name: "Billing admins",
	description: "Manage billing",
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
	holders: 5,
	permissions: ["agents.read", "forms.readwrite"],
	placements: { organizations: 3, managed: false, platform: true },
};

function page(items: object[]) {
	return {
		data: { items, total: items.length },
		isLoading: false,
		isFetching: false,
		isError: false,
		refetch: vi.fn(),
	};
}

const longRole = {
	...role,
	id: "role-2",
	name: "Enterprise security and finance operations administrators with extremely long names",
	description:
		"Oversees access, billing, and policy controls across large deployments",
};

describe("Roles", () => {
	beforeEach(() => {
		authz.canManage = true;
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
		mockUseRolesPage.mockReturnValue(page([longRole]));

		renderWithProviders(<Roles />);

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		const name = screen.getByRole("link", {
			name: /enterprise security and finance operations administrators/i,
		});
		expect(name.getAttribute("class")).not.toContain("truncate");
		expect(
			screen.getByRole("button", {
				name: /enterprise security and finance operations administrators.*actions/i,
			}),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: /sort descending/i }),
		).toHaveClass("h-11", "w-11");
	});

	it("keeps grants, holders and placements on mobile records", () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseRolesPage.mockReturnValue(page([role]));

		renderWithProviders(<Roles />);

		const record = screen.getByRole("article");
		expect(
			within(
				within(record).getByRole("list", {
					name: "What Billing admins grants",
				}),
			).getAllByRole("listitem"),
		).toHaveLength(2);
		expect(
			within(record).getByText("Holders").nextSibling,
		).toHaveTextContent("5");
		expect(within(record).getByText("3 organizations")).toBeVisible();
		expect(within(record).getByText("Global")).toBeVisible();
	});

	it("groups built-in and custom roles under their own headings", () => {
		mockUseRolesPage.mockReturnValue(
			page([
				role,
				{
					...role,
					id: "operator",
					name: "Platform Operator",
					is_builtin: true,
				},
			]),
		);
		renderWithProviders(<Roles />);

		const builtIn = screen.getByRole("rowgroup", { name: "Built-in" });
		const custom = screen.getByRole("rowgroup", { name: "Custom" });
		expect(within(builtIn).getByText("Platform Operator")).toBeVisible();
		expect(within(builtIn).queryByText("Billing admins")).toBeNull();
		expect(within(custom).getByText("Billing admins")).toBeVisible();
		expect(
			builtIn.compareDocumentPosition(custom) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
	});

	it("shows what each role grants in plain words, four at a time", () => {
		mockUseRolesPage.mockReturnValue(
			page([
				{
					...role,
					permissions: [
						"agents.read",
						"forms.readwrite",
						"roles.readwrite",
						"tables.read",
						"workflows.execute",
						"workflows.read",
					],
				},
			]),
		);
		renderWithProviders(<Roles />);

		const grants = screen.getByRole("list", {
			name: "What Billing admins grants",
		});
		expect(
			within(grants)
				.getAllByRole("listitem")
				.map((item) => item.textContent),
		).toEqual(["Agentsview", "Formsmanage", "Rolesmanage", "Tablesview"]);
		expect(within(grants).getByText("Roles").parentElement).toHaveAttribute(
			"data-variant",
			"privileged",
		);
		const more = screen.getByRole("link", { name: "2 more permissions" });
		expect(more).toHaveTextContent("+2");
		expect(more).toHaveAttribute("href", "/roles/role-1/permissions");
	});

	it("shows holders and where each role is placed", () => {
		mockUseRolesPage.mockReturnValue(page([role]));
		renderWithProviders(<Roles />);

		for (const heading of ["Grants", "Holders", "Placed"]) {
			expect(
				screen.getByRole("columnheader", { name: heading }),
			).toBeInTheDocument();
		}
		const row = screen.getByRole("row", { name: /Billing admins/ });
		expect(within(row).getByText("5")).toBeVisible();
		const places = within(row).getByRole("list", {
			name: "Where Billing admins applies",
		});
		expect(
			within(places)
				.getAllByRole("listitem")
				.map((item) => item.textContent),
		).toEqual(["3 organizations", "Global"]);
		expect(
			within(places).getByText("Global").closest("[data-place]"),
		).toHaveClass("bg-[var(--bf-reach-soft)]");
	});

	it("says when a role is placed nowhere yet", () => {
		mockUseRolesPage.mockReturnValue(
			page([
				{
					...role,
					placements: {
						organizations: 0,
						managed: false,
						platform: false,
					},
				},
			]),
		);
		renderWithProviders(<Roles />);

		expect(screen.getByText("Not placed")).toBeVisible();
	});

	it("hides grants, holders and placements when the server leaves them out", () => {
		mockUseRolesPage.mockReturnValue(
			page([
				{
					...role,
					consumer_counts: null,
					holders: null,
					permissions: null,
					placements: null,
				},
			]),
		);
		renderWithProviders(<Roles />);

		for (const heading of ["Grants", "Holders", "Placed"]) {
			expect(
				screen.queryByRole("columnheader", { name: heading }),
			).not.toBeInTheDocument();
		}
		expect(screen.getByText("Billing admins")).toBeVisible();
	});

	it("shows built-in roles read-only, with no edit or delete actions", () => {
		mockUseRolesPage.mockReturnValue(
			page([
				{
					...role,
					id: "operator",
					name: "Platform Operator",
					is_builtin: true,
				},
				role,
			]),
		);
		renderWithProviders(<Roles />);

		expect(
			screen.getByRole("link", { name: "Platform Operator" }),
		).toHaveAttribute("href", "/roles/operator");
		expect(
			screen.queryByRole("button", { name: "Platform Operator actions" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Billing admins actions" }),
		).toBeInTheDocument();
	});

	it("hides role management from callers who can only view roles", () => {
		authz.canManage = false;
		mockUseRolesPage.mockReturnValue({
			data: { items: [{ ...role, consumer_counts: null }], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		renderWithProviders(<Roles />);

		expect(
			screen.queryByRole("button", { name: "Create role" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Billing admins actions" }),
		).not.toBeInTheDocument();
	});
});
