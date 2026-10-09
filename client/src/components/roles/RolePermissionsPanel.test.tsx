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
	"userlifecycle.readwrite",
	"users.read",
	"users.readwrite",
];
const PRIVILEGED = new Set([
	"users.readwrite",
	"userlifecycle.readwrite",
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
		names = {
			[`${domain}.read`]: `Read ${title}`,
			[`${domain}.readwrite`]: `Read and Write ${title}`,
		},
	}: {
		enforced?: boolean;
		description?: string;
		names?: Record<string, string>;
	} = {},
): PermissionCatalogEntry {
	return {
		domain,
		title,
		area,
		description,
		who_should_hold: "Anyone who needs it.",
		actions: ["read", "readwrite"],
		names,
		privileged: [...PRIVILEGED].filter((p) => p.startsWith(`${domain}.`)),
		scope: "per_organization",
		enforced,
	};
}

// Sorted by area then title, as the server sends it.
const CATALOG: PermissionCatalogEntry[] = [
	entry("agents", "Agents", "Automation"),
	entry("tables", "Tables", "Data & Content"),
	entry("userlifecycle", "User Lifecycle", "Identity & Access", {
		names: {
			"userlifecycle.readwrite":
				"Read and Write User Lifecycle",
		},
	}),
	entry("organizations", "Organizations", "Identity & Access", {
		enforced: true,
	}),
	entry("roleassignments", "Role Assignments", "Identity & Access", {
		enforced: true,
	}),
	entry("roles", "Roles", "Identity & Access", {
		enforced: true,
		description:
			"Role definitions. Assigning roles to users is `roleassignments`.",
	}),
	entry("users", "Users", "Identity & Access", { enforced: true }),
	entry("configs", "Configuration", "Integrations & Secrets", {
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
			"Identity & Access",
			"Automation",
			"Data & Content",
			"Integrations & Secrets",
		]);
		const identity = screen.getByRole("region", {
			name: "Identity & Access",
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
			within(domain("Users")).getByRole("radio", { name: "Read Users" }),
		).toBeChecked();
		expect(
			within(domain("Organizations")).getByRole("radio", {
				name: "No Access",
			}),
		).toBeChecked();
		const save = screen.getByRole("button", { name: "Save Permissions" });
		expect(save).toBeDisabled();

		await user.click(
			within(domain("Users")).getByRole("radio", {
				name: "Read and Write Users",
			}),
		);
		await user.click(
			within(domain("Organizations")).getByRole("radio", {
				name: "Read Organizations",
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
			within(domain("User Lifecycle"))
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
		expect(within(domain("Roles")).getByText("No Access")).toBeVisible();
		expect(within(domain("Users")).getAllByRole("radio")).toHaveLength(3);
		expect(within(domain("Agents")).queryByRole("radio")).toBeNull();
	});

	it("names what the role holds in a read-only domain", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(domain("Agents")).getByText("Read and Write Agents"),
		).toBeVisible();
		expect(within(domain("Tables")).getByText("No Access")).toBeVisible();
	});

	it("says once on an area when every domain there takes effect with R3b", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		const automation = screen.getByRole("region", { name: "Automation" });
		expect(
			within(automation).getAllByText("Takes Effect with R3b"),
		).toHaveLength(1);
		expect(
			within(domain("Agents")).queryByText("Takes Effect with R3b"),
		).toBeNull();
	});

	it("says it per domain in an area where only some take effect with R3b", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		const identity = screen.getByRole("region", {
			name: "Identity & Access",
		});
		expect(
			within(identity).getAllByText("Takes Effect with R3b"),
		).toHaveLength(1);
		expect(
			within(domain("User Lifecycle")).getByText("Takes Effect with R3b"),
		).toBeVisible();
		expect(
			within(domain("Users")).queryByText("Takes Effect with R3b"),
		).toBeNull();
	});

	it("shows backticked names in descriptions as code", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		const roles = domain("Roles");
		const code = within(roles).getByText("roleassignments");
		expect(code.tagName).toBe("CODE");
		expect(code).toHaveClass("font-mono");
		expect(roles.textContent).not.toContain("`");
	});

	it("offers a choice only where every permission behind it is editable", () => {
		state.data = {
			...permissionsOf(["users.read"]),
			identity_permissions: IDENTITY.map((p) =>
				item(p, p !== "roles.readwrite"),
			),
		};
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(within(domain("Roles")).queryByRole("radio")).toBeNull();
		expect(within(domain("Roles")).getByText("No Access")).toBeVisible();
	});

	it("shows only the highest level held in a read-only domain", () => {
		state.data = permissionsOf(["agents.read", "agents.readwrite"]);
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(domain("Agents")).getByText("Read and Write Agents"),
		).toBeVisible();
		expect(within(domain("Agents")).queryByText("Read Agents")).toBeNull();
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
			within(users).getByRole("radio", { name: "Read and Write Users" })
				.parentElement?.textContent,
		).toContain("Privileged");
		expect(
			within(users).getByRole("radio", { name: "Read Users" })
				.parentElement?.textContent,
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
			expect(areaHeadings()).toEqual(["Integrations & Secrets"]),
		);
		expect(domain("Configuration")).toBeVisible();

		await user.clear(search);
		await user.type(search, "role assign");
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
				name: "Read Organizations",
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
			screen.getByRole("button", { name: "Save Permissions" }),
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
		expect(
			within(domain("Users")).getByText("Read and Write Users"),
		).toBeVisible();
		expect(
			within(domain("Configuration")).getByText("Read Configuration"),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "Save Permissions" }),
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
		expect(within(domain("Users")).getByText("Read Users")).toBeVisible();
	});

	it("shows the server's refusal inline", async () => {
		state.mutateAsync.mockRejectedValue({
			detail: "Built-in roles can't be changed",
		});
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);
		await user.click(
			within(domain("Roles")).getByRole("radio", { name: "Read Roles" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save Permissions" }),
		);

		expect(
			await screen.findByText("Built-in roles can't be changed"),
		).toBeInTheDocument();
	});
});
