import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import type { components } from "@/lib/v1";

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
	isPlatformAdmin: true,
	baseRoleId: "admin-role",
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

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		isPlatformAdmin: state.isPlatformAdmin,
		authorization: { base_role: { id: state.baseRoleId } },
		meets: () => state.canManage,
	}),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { RolePermissionsPanel } from "./RolePermissionsPanel";

function area(name: string) {
	return screen.getByRole("group", { name });
}

beforeEach(() => {
	state.data = permissionsOf(["users.read", "agents.readwrite"]);
	state.canManage = true;
	state.isPlatformAdmin = true;
	state.baseRoleId = "admin-role";
	state.mutateAsync.mockReset();
	state.mutateAsync.mockResolvedValue(state.data);
});

describe("RolePermissionsPanel", () => {
	it("shows one choice per area and saves only identity permissions", async () => {
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			within(area("Users")).getByRole("radio", { name: "View" }),
		).toBeChecked();
		expect(
			within(area("Organizations")).getByRole("radio", {
				name: "No access",
			}),
		).toBeChecked();
		const save = screen.getByRole("button", { name: "Save permissions" });
		expect(save).toBeDisabled();

		await user.click(
			within(area("Users")).getByRole("radio", {
				name: "View & support",
			}),
		);
		await user.click(
			within(area("Organizations")).getByRole("radio", { name: "View" }),
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

	it("marks privileged choices and explains what that means", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			screen.getByText(/becomes a protected account/i),
		).toBeInTheDocument();
		const users = area("Users");
		expect(
			within(users).getByRole("radio", { name: "View & support" })
				.parentElement?.textContent,
		).toContain("Privileged");
		expect(
			within(users).getByRole("radio", { name: "View" }).parentElement
				?.textContent,
		).not.toContain("Privileged");
	});

	it("lists permissions managed elsewhere read-only", () => {
		renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);

		expect(
			screen.getByText(/managed elsewhere — not editable here yet/i),
		).toBeInTheDocument();
		expect(screen.getByText("agents.readwrite")).toBeInTheDocument();
	});

	it("shows a built-in role's permissions without edit controls", () => {
		state.data = permissionsOf(
			["users.read", "users.readwrite", "configs.read"],
			{ isBuiltin: true },
		);
		renderWithProviders(
			<RolePermissionsPanel roleId="operator" isBuiltin />,
		);

		expect(
			screen.getByText("Built-in roles can't be changed."),
		).toBeInTheDocument();
		expect(screen.queryByRole("radio")).not.toBeInTheDocument();
		expect(within(area("Users")).getByText("View & support")).toBeVisible();
		expect(screen.getByText("configs.read")).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Save permissions" }),
		).not.toBeInTheDocument();
	});

	it("describes Platform Admin instead of listing nothing", () => {
		state.data = permissionsOf([], { isBuiltin: true });
		renderWithProviders(
			<RolePermissionsPanel roleId="admin-role" isBuiltin />,
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
	});

	it("shows the server's refusal inline", async () => {
		state.mutateAsync.mockRejectedValue({
			detail: "Built-in roles can't be changed",
		});
		const { user } = renderWithProviders(
			<RolePermissionsPanel roleId="role-1" isBuiltin={false} />,
		);
		await user.click(
			within(area("Role definitions")).getByRole("radio", {
				name: "View",
			}),
		);
		await user.click(
			screen.getByRole("button", { name: "Save permissions" }),
		);

		expect(
			await screen.findByText("Built-in roles can't be changed"),
		).toBeInTheDocument();
	});
});
