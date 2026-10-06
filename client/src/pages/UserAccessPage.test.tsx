import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import { ApiError } from "@/lib/api-error";
import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import type { PermissionCatalogEntry, UserAccessMap } from "@/services/access";

const mockUseMediaQuery = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));
afterEach(() => mockUseMediaQuery.mockReturnValue(false));

const mockUseUser = vi.fn();
// The account actions are the Users list's own hook; only the requests are stubbed.
const mutations = vi.hoisted(() => ({
	deleteUser: vi.fn(),
	resetMfa: vi.fn(),
	idle: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useUsers", () => ({
	useUser: (...args: unknown[]) => mockUseUser(...args),
	useUpdateUser: () => mutations.idle(),
	useDeleteUser: () => ({
		...mutations.idle(),
		mutateAsync: mutations.deleteUser,
	}),
	useResetUserMfa: () => ({
		...mutations.idle(),
		mutateAsync: mutations.resetMfa,
	}),
	useSignOutUserEverywhere: () => mutations.idle(),
}));
vi.mock("@/hooks/useUserInvites", () => ({
	useResendInvite: () => mutations.idle(),
	useRegenerateInvite: () => mutations.idle(),
	useRevokeInvite: () => mutations.idle(),
	useSendInvite: () => mutations.idle(),
}));
vi.mock("@/services/events", () => ({
	useEventSources: () => ({ data: undefined }),
}));

const mockUseUserAccessMap = vi.fn();
const mockUsePermissionCatalog = vi.fn();
vi.mock("@/services/access", () => ({
	useUserAccessMap: (...args: unknown[]) => mockUseUserAccessMap(...args),
	usePermissionCatalog: (...args: unknown[]) =>
		mockUsePermissionCatalog(...args),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-1", name: "Contoso", is_provider: false }],
	}),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { id: "caller" } }),
}));

const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
	loading: false,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: authz.summary,
		isLoading: authz.loading,
		isPlatformAdmin: authz.summary?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authz.summary, requirement),
	}),
}));

vi.mock("@/components/users/UserProfileForm", () => ({
	UserProfileForm: ({ user }: { user: { email: string } }) => (
		<p>Profile form for {user.email}</p>
	),
}));

vi.mock("@/components/users/UserRoleAssignmentsPanel", () => ({
	UserRoleAssignmentsPanel: () => <p>Role assignments editor</p>,
}));

import { UserAccessPage } from "./UserAccessPage";

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

const person = {
	id: "user-1",
	email: "avery@contoso.example",
	name: "Avery Example",
	is_active: true,
	is_superuser: false,
	is_external: false,
	is_protected: false,
	organization_id: "org-1",
	invite_status: "active",
	created_at: "2026-06-01T00:00:00Z",
	updated_at: "2026-06-01T00:00:00Z",
	last_login: null,
};

const accessMap: UserAccessMap = {
	user_id: "user-1",
	name: "Avery Example",
	email: "avery@contoso.example",
	home_organization: { id: "org-1", name: "Contoso" },
	is_platform_admin: false,
	is_protected: false,
	privileged_permissions: [],
	reach: [
		{
			kind: "home",
			organization_id: "org-1",
			organization_name: "Contoso",
			label: "Contoso (Home)",
		},
		{
			kind: "platform",
			organization_id: null,
			organization_name: null,
			label: "Global",
		},
	],
	rows: [
		{
			place: {
				kind: "home",
				organization_id: "org-1",
				organization_name: "Contoso",
				label: "Contoso (Home)",
			},
			grants: [
				{
					permission: "tables.read",
					domain: "tables",
					action: "read",
					scope: "per_organization",
					sources: [
						{ role_id: "role-1", role_name: "User", via: "base" },
					],
				},
			],
		},
	],
};

const catalog: PermissionCatalogEntry[] = [
	{
		domain: "roles",
		title: "Roles",
		area: "Identity & Access",
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		names: {
			"roles.read": "Read Roles",
			"roles.readwrite": "Read and Write Roles",
		},
		privileged: ["roles.readwrite"],
		scope: "platform_wide",
		enforced: true,
	},
	{
		domain: "users",
		title: "Users",
		area: "Identity & Access",
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		names: {
			"users.read": "Read Users",
			"users.readwrite": "Read and Write Users",
		},
		privileged: ["users.readwrite"],
		scope: "per_organization",
		enforced: true,
	},
	{
		domain: "tables",
		title: "Tables",
		area: "Data & Content",
		description: "",
		who_should_hold: "",
		actions: ["read"],
		names: { "tables.read": "Read Tables" },
		privileged: [],
		scope: "per_organization",
		enforced: true,
	},
];

function LocationProbe() {
	return <output aria-label="location">{useLocation().pathname}</output>;
}

function renderPage(path = "/users/user-1") {
	return renderWithProviders(
		<>
			<Routes>
				<Route path="/users/:userId" element={<UserAccessPage />} />
				<Route
					path="/users/:userId/:tab"
					element={<UserAccessPage />}
				/>
			</Routes>
			<LocationProbe />
		</>,
		{ initialEntries: [path] },
	);
}

beforeEach(() => {
	mutations.deleteUser.mockReset().mockResolvedValue(undefined);
	mutations.resetMfa.mockReset();
	authz.summary = adminSummary();
	authz.loading = false;
	mockUseUser.mockReturnValue({
		data: person,
		isLoading: false,
		isError: false,
		error: null,
	});
	mockUseUserAccessMap.mockReturnValue({
		data: accessMap,
		isLoading: false,
		isError: false,
	});
	mockUsePermissionCatalog.mockReturnValue({
		data: catalog,
		isLoading: false,
		isError: false,
	});
});

describe("UserAccessPage", () => {
	it("introduces the person with where their access reaches", () => {
		renderPage();

		expect(
			screen.getByRole("heading", { level: 1, name: "Avery Example" }),
		).toBeInTheDocument();
		expect(screen.getByText("avery@contoso.example")).toBeInTheDocument();
		const reach = screen.getByRole("list", { name: "Reach" });
		expect(
			within(reach)
				.getAllByRole("listitem")
				.map((item) => item.textContent),
		).toEqual(["Contoso (Home)", "Global"]);
		expect(screen.queryByText(/^Protected:/)).not.toBeInTheDocument();
	});

	it("offers the Users list's account actions, without Edit Profile", async () => {
		const { user } = renderPage();

		await user.click(
			screen.getByRole("button", { name: "Avery Example actions" }),
		);

		for (const name of [
			"Reset MFA",
			"Sign Out of All Devices",
			"Disable",
			"Delete",
		]) {
			expect(screen.getByRole("menuitem", { name })).toBeInTheDocument();
		}
		expect(
			screen.queryByRole("menuitem", { name: "Edit Profile" }),
		).not.toBeInTheDocument();
	});

	it("returns focus to the actions button when a confirmation closes", async () => {
		const { user } = renderPage();
		const actions = screen.getByRole("button", {
			name: "Avery Example actions",
		});

		await user.click(actions);
		await user.click(screen.getByRole("menuitem", { name: "Reset MFA" }));
		await user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", {
				name: "Cancel",
			}),
		);

		await waitFor(() =>
			expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
		);
		expect(actions).toHaveFocus();
		expect(mutations.resetMfa).not.toHaveBeenCalled();
	});

	it("returns to the Users list once the person is deleted", async () => {
		const { user } = renderPage();

		await user.click(
			screen.getByRole("button", { name: "Avery Example actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", {
				name: "Permanently Delete",
			}),
		);

		await waitFor(() =>
			expect(
				screen.getByRole("status", { name: "location" }),
			).toHaveTextContent(/^\/users$/),
		);
		expect(mutations.deleteUser).toHaveBeenCalledWith({
			params: { path: { user_id: "user-1" } },
		});
	});

	it("names its sections, with what each shows in the description", () => {
		renderPage();

		expect(
			screen.getByRole("heading", { level: 2, name: "Effective Access" }),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				"What this person can do, in each organization their roles reach. Hover a permission to see which role grants it.",
			),
		).toBeInTheDocument();
		expect(
			screen.getByRole("heading", { level: 2, name: "Role Assignments" }),
		).toBeInTheDocument();
	});

	it("opens on the access map above the role assignments", () => {
		renderPage();

		expect(screen.getByRole("tab", { name: "Access" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		const table = screen.getByRole("table", { name: "Access by Place" });
		expect(
			within(table).getByRole("rowheader", { name: "Contoso (Home)" }),
		).toBeInTheDocument();
		expect(
			within(table).getByRole("columnheader", { name: "Data & Content" }),
		).toBeInTheDocument();
		expect(screen.getByText("Role assignments editor")).toBeInTheDocument();
		expect(mockUseUserAccessMap).toHaveBeenLastCalledWith("user-1");
	});

	it("waits for the catalog before drawing the map", () => {
		mockUsePermissionCatalog.mockReturnValue({
			data: undefined,
			isLoading: true,
			isError: false,
		});
		renderPage();

		expect(
			screen.getByRole("status", { name: "Loading access" }),
		).toBeInTheDocument();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
	});

	it("says why a protected person is protected", () => {
		mockUseUser.mockReturnValue({
			data: { ...person, is_protected: true },
			isLoading: false,
			isError: false,
		});
		mockUseUserAccessMap.mockReturnValue({
			data: {
				...accessMap,
				is_protected: true,
				privileged_permissions: ["roles.readwrite", "users.readwrite"],
			},
			isLoading: false,
			isError: false,
		});
		renderPage();

		expect(
			screen.getByText(
				"Protected: holds Read and Write Roles and Read and Write Users. Only a Platform Admin can change this account.",
			),
		).toBeInTheDocument();
	});

	it("moves between tabs through the address", async () => {
		const { user } = renderPage();

		await user.click(screen.getByRole("tab", { name: "Profile" }));

		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/user-1/profile");
		expect(
			screen.getByText("Profile form for avery@contoso.example"),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Role assignments editor"),
		).not.toBeInTheDocument();
	});

	it("opens the Profile tab from its address", () => {
		renderPage("/users/user-1/profile");

		expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
	});

	it("lists one record per place on narrow screens", () => {
		mockUseMediaQuery.mockReturnValue(true);
		renderPage();

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		const records = within(
			screen.getByRole("list", { name: "Access by Place" }),
		).getAllByRole("listitem");
		expect(records).toHaveLength(1);
		expect(
			within(records[0]).getByRole("heading", { name: "Contoso (Home)" }),
		).toBeInTheDocument();
	});

	it("shows only the profile to someone who can't see role assignments", () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
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
		renderPage();

		expect(
			screen.queryByRole("tab", { name: "Access" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByText("Profile form for avery@contoso.example"),
		).toBeInTheDocument();
		expect(mockUseUserAccessMap).toHaveBeenLastCalledWith(undefined);
	});

	it("moves someone who can't see role assignments from the access address to the profile", () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
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
		renderPage("/users/user-1/access");

		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/user-1/profile");
		expect(
			screen.getByText("Profile form for avery@contoso.example"),
		).toBeInTheDocument();
	});

	it("keeps the address while the caller's own access is still loading", () => {
		authz.summary = undefined;
		authz.loading = true;
		renderPage("/users/user-1/access");

		expect(
			screen.getByRole("status", { name: "Loading user" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("status", { name: "location" }),
		).toHaveTextContent("/users/user-1/access");
	});

	it("explains when the person is out of the caller's reach", () => {
		mockUseUser.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			error: new ApiError("Not allowed", 403),
		});
		renderPage();

		expect(
			screen.getByRole("heading", { name: "You can't view this person" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: "Back to Users" }),
		).toHaveAttribute("href", "/users");
	});

	it("explains when the person doesn't exist", () => {
		mockUseUser.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			error: new ApiError("User not found", 404),
		});
		renderPage();

		expect(
			screen.getByRole("heading", { name: "User Not Found" }),
		).toBeInTheDocument();
	});

	it("offers a retry when loading fails for another reason", async () => {
		const refetch = vi.fn();
		mockUseUser.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			error: new ApiError("Service unavailable", 503),
			refetch,
		});
		const { user } = renderPage();

		expect(
			screen.getByRole("heading", { name: "Couldn't load this person" }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Try Again" }));
		expect(refetch).toHaveBeenCalledOnce();
	});
});
