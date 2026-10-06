import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import type { components } from "@/lib/v1";
import type { PermissionCatalogEntry } from "@/services/access";

type Permissions = components["schemas"]["RolePermissionsResponse"];

const IDENTITY = [
	"organizations.read",
	"organizations.readwrite",
	"roleassignments.read",
	"roleassignments.readwrite",
	"roles.read",
	"roles.readwrite",
	"users.lifecycle.readwrite",
	"users.read",
	"users.readwrite",
];
const PRIVILEGED = new Set([
	"users.readwrite",
	"users.lifecycle.readwrite",
	"roles.readwrite",
	"roleassignments.readwrite",
	"organizations.readwrite",
	"configs.readwrite",
]);

function entry(
	domain: string,
	title: string,
	area: PermissionCatalogEntry["area"],
	{
		enforced = false,
		description = `What ${title} covers.`,
	}: { enforced?: boolean; description?: string } = {},
): PermissionCatalogEntry {
	return {
		domain,
		title,
		area,
		description,
		who_should_hold: "Anyone who needs it.",
		actions: ["read", "readwrite"],
		privileged: [...PRIVILEGED].filter((p) => p.startsWith(`${domain}.`)),
		scope: "per_organization",
		enforced,
	};
}

// Sorted by area then title, as the server sends it.
const CATALOG: PermissionCatalogEntry[] = [
	entry("agents", "Agents", "Automation"),
	entry("tables", "Tables", "Data & content"),
	entry(
		"users.lifecycle",
		"Move, delete & change base role",
		"Identity & access",
		{
			enforced: true,
		},
	),
	entry("organizations", "Organizations", "Identity & access", {
		enforced: true,
	}),
	entry("roleassignments", "Role assignments", "Identity & access", {
		enforced: true,
	}),
	entry("roles", "Roles", "Identity & access", { enforced: true }),
	entry("users", "Users", "Identity & access", { enforced: true }),
	entry("configs", "Configs", "Integrations & secrets", {
		description: "Configuration values and secret references.",
	}),
];

function item(permission: string, editable: boolean) {
	return { permission, editable, privileged: PRIVILEGED.has(permission) };
}

function permissionsOf(
	held: string[],
	{ isBuiltin = false }: { isBuiltin?: boolean } = {},
): Permissions {
	return {
		role_id: "role-1",
		is_builtin: isBuiltin,
		permissions: held.map((p) =>
			item(p, !isBuiltin && IDENTITY.includes(p)),
		),
		identity_permissions: IDENTITY.map((p) => item(p, !isBuiltin)),
	};
}

const state = vi.hoisted(() => ({
	data: undefined as Permissions | undefined,
	canManage: true,
	mutateAsync: vi.fn(),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRolePermissions: () => ({
		data: state.data,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useUpdateRolePermissions: () => ({
		mutateAsync: state.mutateAsync,
		isPending: false,
	}),
}));

vi.mock("@/services/access", () => ({
	usePermissionCatalog: () => ({
		data: CATALOG,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		meets: () => state.canManage,
	}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { RolePermissionsPanel } from "./RolePermissionsPanel";

function domain(name: string) {
	return screen.getByRole("group", { name });
}

function areaHeadings() {
	return screen
		.getAllByRole("heading", { level: 2 })
		.map((heading) => heading.textContent);
}

beforeEach(() => {
	state.data = permissionsOf(["users.read", "agents.readwrite"]);
	state.canManage = true;
	state.mutateAsync.mockReset();
	state.mutateAsync.mockResolvedValue(state.data);
});

describe("RolePermissionsPanel", () => {
	it("groups every catalog domain by area, editable areas first", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(areaHeadings()).toEqual([
			"Identity & access",
			"Automation",
			"Data & content",
			"Integrations & secrets",
		]);
		const identity = screen.getByRole("region", {
			name: "Identity & access",
		});
		expect(
			within(identity).getByRole("group", { name: "Users" }),
		).toBeVisible();
		expect(
			within(
				screen.getByRole("region", { name: "Automation" }),
			).getByRole("group", { name: "Agents" }),
		).toBeVisible();
	});

	it("offers a choice per identity domain and saves only identity permissions", async () => {
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(domain("Users")).getByRole("radio", { name: "View" }),
		).toBeChecked();
		expect(
			within(domain("Organizations")).getByRole("radio", {
				name: "No access",
			}),
		).toBeChecked();
		const save = screen.getByRole("button", { name: "Save permissions" });
		expect(save).toBeDisabled();

		await user.click(
			within(domain("Users")).getByRole("radio", { name: "Manage" }),
		);
		await user.click(
			within(domain("Organizations")).getByRole("radio", {
				name: "View",
			}),
		);
		await user.click(save);

		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0]).toEqual({
			params: { path: { role_id: "role-1" } },
			body: {
				permissions: [
					"organizations.read",
					"users.read",
					"users.readwrite",
				],
			},
		});
	});

	it("offers only the actions a domain has", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(domain("Move, delete & change base role"))
				.getAllByRole("radio")
				.map((radio) => radio.getAttribute("value")),
		).toEqual(["none", "readwrite"]);
	});

	it("is editable exactly where the server says", () => {
		state.data = {
			...permissionsOf(["users.read"]),
			identity_permissions: IDENTITY.map((p) =>
				item(p, !p.startsWith("roles.")),
			),
		};
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(within(domain("Roles")).queryByRole("radio")).toBeNull();
		expect(within(domain("Roles")).getByText("No access")).toBeVisible();
		expect(within(domain("Users")).getAllByRole("radio")).toHaveLength(3);
		expect(within(domain("Agents")).queryByRole("radio")).toBeNull();
	});

	it("shows what the role holds in a read-only domain, in plain words", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(within(domain("Agents")).getByText("Manage")).toBeVisible();
		expect(within(domain("Tables")).getByText("No access")).toBeVisible();
	});

	it("says which domains take effect with R3b", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(domain("Agents")).getByText("Takes effect with R3b"),
		).toBeVisible();
		expect(
			within(domain("Users")).queryByText("Takes effect with R3b"),
		).toBeNull();
	});

	it("marks privileged choices and explains what that means", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			screen.getByText(/becomes a protected account/i),
		).toBeInTheDocument();
		const users = domain("Users");
		expect(
			within(users).getByRole("radio", { name: "Manage" }).parentElement
				?.textContent,
		).toContain("Privileged");
		expect(
			within(users).getByRole("radio", { name: "View" }).parentElement
				?.textContent,
		).not.toContain("Privileged");
	});

	it("filters domains by title, domain or description", async () => {
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		const search = screen.getByRole("textbox", {
			name: "Search permissions",
		});
		await user.type(search, "secret");
		await waitFor(() =>
			expect(areaHeadings()).toEqual(["Integrations & secrets"]),
		);
		expect(domain("Configs")).toBeVisible();

		await user.clear(search);
		await user.type(search, "roleassign");
		await waitFor(() =>
			expect(
				screen
					.getAllByRole("group")
					.map((group) => group.getAttribute("aria-labelledby")),
			).toEqual(["permission-roleassignments"]),
		);

		await user.clear(search);
		await user.type(search, "nothing like this");
		expect(
			await screen.findByText("No permissions match your search."),
		).toBeVisible();
	});

	it("keeps unsaved choices for domains the search hides", async () => {
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		await user.click(
			within(domain("Organizations")).getByRole("radio", {
				name: "View",
			}),
		);
		await user.type(
			screen.getByRole("textbox", { name: "Search permissions" }),
			"users",
		);
		await waitFor(() =>
			expect(
				screen.queryByRole("group", { name: "Organizations" }),
			).toBeNull(),
		);
		await user.click(
			screen.getByRole("button", { name: "Save permissions" }),
		);

		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0].body.permissions).toEqual([
			"organizations.read",
			"users.read",
		]);
	});

	it("shows a built-in role's permissions without edit controls", () => {
		state.data = permissionsOf(
			["users.read", "users.readwrite", "configs.read"],
			{ isBuiltin: true },
		);
		renderWithProviders(
			<RolePermissionsPanel roleId="operator" isBuiltin />,
		);

		expect(screen.queryByRole("radio")).not.toBeInTheDocument();
		expect(within(domain("Users")).getByText("Manage")).toBeVisible();
		expect(within(domain("Configs")).getByText("View")).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "Save permissions" }),
		).not.toBeInTheDocument();
	});

	it("describes Platform Admin instead of listing nothing", () => {
		state.data = permissionsOf([], { isBuiltin: true });
		renderWithProviders(
			<RolePermissionsPanel
				roleId="00000000-0000-0000-0000-000000000005"
				isBuiltin
			/>,
		);

		expect(
			screen.getByText(/holds every permission except reading secrets/i),
		).toBeInTheDocument();
	});

	it("is read-only for callers who can only view roles", () => {
		state.canManage = false;
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			screen.getByText(
				/can view this role's permissions but not change/i,
			),
		).toBeInTheDocument();
		expect(screen.queryByRole("radio")).not.toBeInTheDocument();
		expect(within(domain("Users")).getByText("View")).toBeVisible();
	});

	it("shows the server's refusal inline", async () => {
		state.mutateAsync.mockRejectedValue({
			detail: "Built-in roles can't be changed",
		});
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);
		await user.click(
			within(domain("Roles")).getByRole("radio", { name: "View" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save permissions" }),
		);

		expect(
			await screen.findByText("Built-in roles can't be changed"),
		).toBeInTheDocument();
	});
});
