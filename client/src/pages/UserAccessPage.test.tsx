import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, within } from "@/test-utils";
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
vi.mock("@/hooks/useUsers", () => ({
	useUser: (...args: unknown[]) => mockUseUser(...args),
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
			label: "Contoso (home)",
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
				label: "Contoso (home)",
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
		title: "Role definitions",
		area: "Identity & access",
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		privileged: ["roles.readwrite"],
		scope: "platform_wide",
		enforced: true,
	},
	{
		domain: "users",
		title: "Users",
		area: "Identity & access",
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		privileged: ["users.readwrite"],
		scope: "per_organization",
		enforced: true,
	},
	{
		domain: "tables",
		title: "Tables",
		area: "Data & content",
		description: "",
		who_should_hold: "",
		actions: ["read"],
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
	authz.summary = adminSummary();
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
		).toEqual(["Contoso (home)", "Global"]);
		expect(screen.queryByText(/^Protected:/)).not.toBeInTheDocument();
	});

	it("opens on the access map above the role assignments", () => {
		renderPage();

		expect(screen.getByRole("tab", { name: "Access" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		const table = screen.getByRole("table", { name: "Access by place" });
		expect(
			within(table).getByRole("rowheader", { name: "Contoso (home)" }),
		).toBeInTheDocument();
		expect(
			within(table).getByRole("columnheader", { name: "Data & content" }),
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
				"Protected: holds Role definitions (manage) and Users (manage). Only a Platform Admin can change this account.",
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
			screen.getByRole("list", { name: "Access by place" }),
		).getAllByRole("listitem");
		expect(records).toHaveLength(1);
		expect(
			within(records[0]).getByRole("heading", { name: "Contoso (home)" }),
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
			screen.getByRole("link", { name: "Back to users" }),
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
			screen.getByRole("heading", { name: "User not found" }),
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
		await user.click(screen.getByRole("button", { name: "Try again" }));
		expect(refetch).toHaveBeenCalledOnce();
	});
});
