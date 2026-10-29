import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import {
	act,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";
import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import type { Identity } from "@/services/identities";

const mockUseMediaQuery = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));
afterEach(() => mockUseMediaQuery.mockReturnValue(false));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true, user: { id: "caller" } }),
}));

const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: authz.summary,
		isPlatformAdmin: authz.summary?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authz.summary, requirement),
	}),
}));

const mockUseIdentities = vi.fn();
const deleteIdentity = vi.hoisted(() => vi.fn());
vi.mock("@/services/identities", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identities")>()),
	useIdentities: () => mockUseIdentities(),
	useDeleteIdentity: () => ({
		mutateAsync: deleteIdentity,
		isPending: false,
	}),
	useRenameIdentity: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/components/users/BulkUserDialogs", async (importOriginal) => ({
	...(await importOriginal<
		typeof import("@/components/users/BulkUserDialogs")
	>()),
	BulkReplaceRolesDialog: (props: {
		open: boolean;
		users: { name?: string | null }[];
	}) =>
		props.open ? (
			<p>Replace roles for {props.users.map((u) => u.name).join(", ")}</p>
		) : null,
}));

const dialog = vi.hoisted(() => ({
	props: undefined as
		| { open: boolean; onCreated: (identity: { id: string }) => void }
		| undefined,
}));
vi.mock("@/components/identities/NewIdentityDialog", () => ({
	NewIdentityDialog: (props: {
		open: boolean;
		onCreated: (identity: { id: string }) => void;
	}) => {
		dialog.props = props;
		return props.open ? <p>New identity dialog</p> : null;
	},
}));

import { Identities } from "./Identities";

const PROVIDER = "org-provider";

function adminSummary(): AuthorizationSummary {
	return {
		is_platform_admin: true,
		home_organization_id: PROVIDER,
		provider_organization_id: PROVIDER,
		base_role: { id: "admin-role", name: "Platform Admin" },
		grants: [],
	};
}

function readerSummary(): AuthorizationSummary {
	return {
		...adminSummary(),
		is_platform_admin: false,
		base_role: { id: "user-role", name: "User" },
		grants: [
			{
				permission: "users.read",
				boundary: {
					kind: "managed_organizations",
					organization_id: null,
				},
			},
		],
	};
}

const identities: Identity[] = [
	{
		id: "identity-global",
		name: "Default Identity",
		identity_kind: "global_default",
		organization_id: null,
		organization_name: null,
		base_role: { id: "role-user", name: "User" },
		additional_roles: [
			{
				role_id: "role-fleet",
				name: "Fleet Operations",
				boundaries: [
					{
						kind: "managed_organizations",
						organization_id: null,
						organization_name: null,
					},
					{
						kind: "platform",
						organization_id: null,
						organization_name: null,
					},
				],
			},
		],
		workflows_using: 2,
	},
	{
		id: "identity-contoso",
		name: "Default Identity",
		identity_kind: "org_default",
		organization_id: "org-1",
		organization_name: "Contoso",
		base_role: { id: "role-user", name: "User" },
		additional_roles: [
			{
				role_id: "role-sync",
				name: "Ticket Sync",
				boundaries: [
					{
						kind: "organization",
						organization_id: "org-1",
						organization_name: "Contoso",
					},
				],
			},
		],
		workflows_using: 12,
	},
	{
		id: "identity-backup",
		name: "Backup Runner",
		identity_kind: "custom",
		organization_id: "org-1",
		organization_name: "Contoso",
		base_role: { id: "role-user", name: "User" },
		additional_roles: [],
		workflows_using: 0,
	},
];

function LocationProbe() {
	return <output aria-label="location">{useLocation().pathname}</output>;
}

function renderPage() {
	return renderWithProviders(
		<>
			<Routes>
				<Route path="/users/identities" element={<Identities />} />
				<Route path="*" element={null} />
			</Routes>
			<LocationProbe />
		</>,
		{ initialEntries: ["/users/identities"] },
	);
}

beforeEach(() => {
	deleteIdentity.mockReset().mockResolvedValue(undefined);
	authz.summary = adminSummary();
	dialog.props = undefined;
	mockUseIdentities.mockReturnValue({
		data: identities,
		isLoading: false,
		isFetching: false,
		isError: false,
		refetch: vi.fn(),
	});
});

describe("Identities", () => {
	it("is the Identities tab beside Users", () => {
		renderPage();

		expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute(
			"href",
			"/users",
		);
		expect(
			screen.getByRole("link", { name: "Identities" }),
		).toHaveAttribute("aria-current", "page");
		expect(
			screen.getByRole("heading", { level: 1, name: "Identities" }),
		).toBeInTheDocument();
	});

	it("lists identities by organization, name, kind, roles and workflows, without email", () => {
		renderPage();

		const table = screen.getByRole("table");
		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual([
			"",
			"Organization",
			"Name",
			"Kind",
			"Roles",
			"Workflows Using",
			"Actions",
		]);
		const [, contoso] = within(table).getAllByRole("row").slice(1);
		expect(
			within(contoso)
				.getAllByRole("cell")
				.map((cell) => cell.textContent),
		).toEqual([
			"",
			"Contoso",
			"Default Identity",
			"Default",
			"UserTicket Sync",
			"12",
			"",
		]);
	});

	it("pins the global identity first, highlighted, as the Default Identity of Global", () => {
		renderPage();

		const rows = within(screen.getByRole("table"))
			.getAllByRole("row")
			.slice(1);
		const cells = within(rows[0]).getAllByRole("cell");
		expect(
			within(cells[1]).getByLabelText("Organization"),
		).toHaveTextContent("Global");
		expect(cells[2]).toHaveTextContent("Default Identity");
		expect(cells[3]).toHaveTextContent("Global");
		expect(rows[0]).toHaveAttribute("data-pinned", "true");
		expect(rows[1]).not.toHaveAttribute("data-pinned");
	});

	it("opens an identity's page from anywhere on its row", async () => {
		const { user } = renderPage();
		const backup = within(screen.getByRole("table"))
			.getAllByRole("row")
			.find((row) => row.textContent?.includes("Backup Runner"))!;

		await user.click(within(backup).getAllByRole("cell")[5]);

		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/identity-backup");
	});

	it("links each name to the identity's page, from the keyboard too", async () => {
		const { user } = renderPage();
		const link = screen.getByRole("link", { name: "Backup Runner" });
		expect(link).toHaveAttribute("href", "/users/identity-backup");

		link.focus();
		await user.keyboard("{Enter}");

		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/identity-backup");
	});

	it("says where each additional role applies", () => {
		renderPage();

		expect(screen.getByText("Fleet Operations")).toHaveAttribute(
			"title",
			"Applies in All Customer Organizations, Global",
		);
		expect(screen.getByText("Ticket Sync")).toHaveAttribute(
			"title",
			"Applies in Contoso",
		);
	});

	it("creates an identity and opens its page", async () => {
		const { user } = renderPage();

		await user.click(screen.getByRole("button", { name: "New Identity" }));
		expect(screen.getByText("New identity dialog")).toBeInTheDocument();

		act(() => dialog.props!.onCreated({ id: "identity-new" }));

		expect(
			await screen.findByText("/users/identity-new"),
		).toBeInTheDocument();
	});

	it("offers New Identity only to those who can create one", () => {
		authz.summary = readerSummary();
		renderPage();

		expect(
			screen.queryByRole("button", { name: "New Identity" }),
		).not.toBeInTheDocument();
	});

	it("lists one record per identity on narrow screens", () => {
		mockUseMediaQuery.mockReturnValue(true);
		renderPage();

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		// Each record's role chips are a list of their own.
		const list = screen.getByRole("list", { name: "Identities" });
		const records = within(list)
			.getAllByRole("listitem")
			.filter((item) => item.parentElement === list);
		expect(records).toHaveLength(3);
		expect(records[0]).toHaveAttribute("data-pinned", "true");
		// The organization is each record's first labeled value.
		const [label] = within(records[1]).getAllByRole("term");
		expect(label).toHaveTextContent("Organization");
		expect(label.nextElementSibling).toHaveTextContent("Contoso");
		expect(within(records[1]).getByText("Ticket Sync")).toBeInTheDocument();
		expect(within(records[1]).getByText("12")).toBeInTheDocument();
	});

	it("says when there are no identities", () => {
		mockUseIdentities.mockReturnValue({
			data: [],
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		renderPage();

		expect(screen.getByText("No Identities")).toBeInTheDocument();
	});

	it("filters by name or organization", async () => {
		const { user } = renderPage();

		await user.type(screen.getByRole("textbox"), "backup");

		await waitFor(() =>
			expect(
				within(screen.getByRole("table")).getAllByRole("row"),
			).toHaveLength(2),
		);
		expect(
			within(screen.getByRole("table")).getAllByRole("row")[1],
		).toHaveTextContent("Backup Runner");
	});

	it("offers Replace Roles and Delete for the selected identities, nothing that only applies to people", async () => {
		const { user } = renderPage();

		await user.click(
			screen.getByRole("checkbox", {
				name: "Select all visible identities",
			}),
		);

		const bar = screen.getByRole("region", {
			name: "Bulk identity actions",
		});
		expect(bar).toHaveTextContent("3 selected");
		expect(
			within(bar)
				.getAllByRole("button")
				.map((button) => button.textContent),
		).toEqual(["", "Replace Roles", "Delete"]);
		await user.click(
			within(bar).getByRole("button", { name: "Replace Roles" }),
		);
		expect(
			screen.getByText(
				"Replace roles for Default Identity, Default Identity, Backup Runner",
			),
		).toBeInTheDocument();
	});

	it("deletes only the custom identities selected, saying how many defaults it skips", async () => {
		const { user } = renderPage();
		await user.click(
			screen.getByRole("checkbox", {
				name: "Select all visible identities",
			}),
		);

		await user.click(
			within(
				screen.getByRole("region", { name: "Bulk identity actions" }),
			).getByRole("button", { name: "Delete" }),
		);
		const dialog = await screen.findByRole("dialog", {
			name: "Delete 1 Identity",
		});
		expect(dialog).toHaveTextContent(
			"2 default identities selected can't be deleted and will be skipped.",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Delete 1 Identity" }),
		);

		await waitFor(() => expect(deleteIdentity).toHaveBeenCalledOnce());
		expect(deleteIdentity).toHaveBeenCalledWith({
			params: { path: { identity_id: "identity-backup" } },
		});
		await waitFor(() =>
			expect(
				screen.queryByRole("region", { name: "Bulk identity actions" }),
			).not.toBeInTheDocument(),
		);
	});

	it("shows which identities couldn't be deleted, and why", async () => {
		deleteIdentity.mockRejectedValue({
			detail: "Can't delete Backup Runner: these workflows run as it: Nightly Sync",
		});
		const { user } = renderPage();
		await user.click(
			screen.getByRole("checkbox", {
				name: "Select Backup Runner · Contoso",
			}),
		);
		await user.click(
			within(
				screen.getByRole("region", { name: "Bulk identity actions" }),
			).getByRole("button", { name: "Delete" }),
		);
		await user.click(
			within(
				await screen.findByRole("dialog", {
					name: "Delete 1 Identity",
				}),
			).getByRole("button", { name: "Delete 1 Identity" }),
		);

		const result = await screen.findByRole("dialog", {
			name: "Bulk action results",
		});
		expect(result).toHaveTextContent("0 succeeded · 1 failed");
		expect(result).toHaveTextContent(
			"Backup RunnerCan't delete Backup Runner: these workflows run as it: Nightly Sync",
		);
	});

	it("can't delete when only default identities are selected", async () => {
		const { user } = renderPage();
		await user.click(
			screen.getByRole("checkbox", {
				name: "Select Default Identity · Contoso",
			}),
		);

		const remove = within(
			screen.getByRole("region", { name: "Bulk identity actions" }),
		).getByRole("button", { name: "Delete" });
		expect(remove).toBeDisabled();
		expect(remove).toHaveAttribute(
			"title",
			"Default identities can't be deleted",
		);
	});

	it("offers Open, Rename and Delete on a custom identity's row, and only Open on a default's", async () => {
		const { user } = renderPage();

		await user.click(
			screen.getByRole("button", {
				name: "Backup Runner · Contoso actions",
			}),
		);
		expect(
			screen.getAllByRole("menuitem").map((item) => item.textContent),
		).toEqual(["Open", "Rename", "Delete"]);
		await user.keyboard("{Escape}");

		const [, contoso] = within(screen.getByRole("table"))
			.getAllByRole("row")
			.slice(1);
		await user.click(
			within(contoso).getByRole("button", {
				name: "Default Identity · Contoso actions",
			}),
		);
		expect(
			screen.getAllByRole("menuitem").map((item) => item.textContent),
		).toEqual(["Open"]);
		await user.click(screen.getByRole("menuitem", { name: "Open" }));
		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/identity-contoso");
	});

	it("offers no selection to someone who can only read identities", () => {
		authz.summary = readerSummary();
		renderPage();

		expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
		expect(
			within(screen.getByRole("table"))
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual([
			"Organization",
			"Name",
			"Kind",
			"Roles",
			"Workflows Using",
			"Actions",
		]);
	});

	it("selects and acts on records on narrow screens", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		const { user } = renderPage();

		await user.click(
			screen.getByRole("checkbox", {
				name: "Select Backup Runner · Contoso",
			}),
		);
		expect(
			screen.getByRole("region", { name: "Bulk identity actions" }),
		).toHaveTextContent("1 selected");
		expect(
			screen.getByRole("checkbox", {
				name: "Select all visible identities",
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "Backup Runner · Contoso actions",
			}),
		).toBeInTheDocument();
	});
});
