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
vi.mock("@/services/identities", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identities")>()),
	useIdentities: () => mockUseIdentities(),
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

	it("lists identities by name, kind, organization, roles and workflows, without email", () => {
		renderPage();

		const table = screen.getByRole("table");
		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((header) => header.textContent),
		).toEqual(["Name", "Kind", "Organization", "Roles", "Workflows Using"]);
		const [, contoso] = within(table).getAllByRole("row").slice(1);
		expect(
			within(contoso)
				.getAllByRole("cell")
				.map((cell) => cell.textContent),
		).toEqual([
			"Default Identity",
			"Default",
			"Contoso",
			"UserTicket Sync",
			"12",
		]);
	});

	it("pins the global identity first, highlighted, as the Default Identity of Global", () => {
		renderPage();

		const rows = within(screen.getByRole("table"))
			.getAllByRole("row")
			.slice(1);
		const cells = within(rows[0]).getAllByRole("cell");
		expect(cells[0]).toHaveTextContent("Default Identity");
		expect(cells[1]).toHaveTextContent("Global");
		expect(
			within(cells[2]).getByLabelText("Organization"),
		).toHaveTextContent("Global");
		expect(rows[0]).toHaveAttribute("data-pinned", "true");
		expect(rows[1]).not.toHaveAttribute("data-pinned");
	});

	it("opens an identity's page from anywhere on its row", async () => {
		const { user } = renderPage();
		const backup = within(screen.getByRole("table"))
			.getAllByRole("row")
			.find((row) => row.textContent?.includes("Backup Runner"))!;

		await user.click(within(backup).getAllByRole("cell")[4]);

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
});
