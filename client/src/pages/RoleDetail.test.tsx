import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";

const state = vi.hoisted(() => ({
	role: undefined as Record<string, unknown> | undefined,
	canManage: true,
	canViewPeople: true,
	deleteError: undefined as unknown,
}));

vi.mock("@/hooks/useRoles", () => ({
	useRole: () => ({
		data: state.role,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useDeleteRole: () => ({
		mutate: vi.fn(),
		reset: vi.fn(),
		isPending: false,
		isError: state.deleteError !== undefined,
		error: state.deleteError,
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		meets: ({ permission }: { permission: string }) =>
			permission === "roleassignments.read"
				? state.canViewPeople
				: state.canManage,
	}),
}));

vi.mock("@/components/roles/RolePermissionsPanel", () => ({
	RolePermissionsPanel: ({ isBuiltin }: { isBuiltin: boolean }) => (
		<p>Permissions panel{isBuiltin ? " (built-in)" : ""}</p>
	),
}));

vi.mock("@/components/roles/RolePeoplePanel", () => ({
	RolePeoplePanel: () => <p>People panel</p>,
}));

vi.mock("@/components/roles/RoleDialog", () => ({
	RoleDialog: () => null,
}));

import { RoleDetail } from "./RoleDetail";

function renderAt(path: string) {
	return renderWithProviders(
		<Routes>
			<Route path="/roles/:roleId" element={<RoleDetail />} />
			<Route path="/roles/:roleId/:tab" element={<RoleDetail />} />
		</Routes>,
		{ initialEntries: [path] },
	);
}

const customRole = {
	id: "role-1",
	name: "Help desk",
	description: "Shares help desk forms",
	is_builtin: false,
	created_by: "admin@example.com",
	created_at: "2026-01-01T00:00:00Z",
	updated_at: "2026-01-01T00:00:00Z",
	consumer_counts: {
		users: 3,
		forms: 1,
		agents: 0,
		apps: 0,
		workflows: 0,
		knowledge: 0,
	},
};

beforeEach(() => {
	state.role = customRole;
	state.canManage = true;
	state.canViewPeople = true;
	state.deleteError = undefined;
});

describe("RoleDetail", () => {
	it("shows a built-in role's permissions read-only, without consumer tabs", () => {
		state.role = {
			...customRole,
			id: "operator",
			name: "Platform Operator",
			is_builtin: true,
			consumer_counts: null,
		};
		renderAt("/roles/operator");

		expect(screen.getByText("Built-in")).toBeInTheDocument();
		expect(
			screen.getByText("Permissions panel (built-in)"),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("tab", { name: /Forms/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Delete" }),
		).not.toBeInTheDocument();
	});

	it("adds a Permissions tab beside a custom role's consumers", () => {
		renderAt("/roles/role-1/permissions");

		expect(screen.getByRole("tab", { name: /Users/ })).toBeInTheDocument();
		expect(
			screen.getByRole("tab", { name: "Permissions" }),
		).toHaveAttribute("aria-selected", "true");
		expect(screen.getByText("Permissions panel")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Edit" }),
		).toBeInTheDocument();
	});

	it("hides edit and delete from callers who can only view roles", () => {
		state.canManage = false;
		renderAt("/roles/role-1/permissions");

		expect(
			screen.queryByRole("button", { name: "Edit" }),
		).not.toBeInTheDocument();
	});

	it("explains why a role can't be deleted", async () => {
		state.deleteError = {
			detail: "This role is the base role of 2 user(s); give them another base role first",
		};
		const { user } = renderAt("/roles/role-1/permissions");
		await user.click(screen.getByRole("button", { name: "Delete" }));

		expect(
			await screen.findByText(/is the base role of 2 user\(s\)/),
		).toBeInTheDocument();
	});

	it("lists who holds a built-in additional role", async () => {
		state.role = {
			...customRole,
			id: "operator",
			name: "Platform Operator",
			is_builtin: true,
			is_base: false,
			consumer_counts: null,
		};
		const { user } = renderAt("/roles/operator");

		expect(
			screen.getByRole("tab", { name: "Permissions" }),
		).toHaveAttribute("aria-selected", "true");
		await user.click(screen.getByRole("tab", { name: "People" }));
		expect(await screen.findByText("People panel")).toBeInTheDocument();
	});

	it("lists who holds Platform Admin, an additional role", async () => {
		state.role = {
			...customRole,
			id: "00000000-0000-0000-0000-000000000005",
			name: "Platform Admin",
			is_builtin: true,
			is_base: false,
			consumer_counts: null,
		};
		const { user } = renderAt(
			"/roles/00000000-0000-0000-0000-000000000005",
		);

		await user.click(screen.getByRole("tab", { name: "People" }));
		expect(await screen.findByText("People panel")).toBeInTheDocument();
	});

	it("says when Secrets Reader takes effect, and lists who holds it", async () => {
		const id = "00000000-0000-0000-0000-000000000008";
		state.role = {
			...customRole,
			id,
			name: "Secrets Reader",
			is_builtin: true,
			is_base: false,
			consumer_counts: null,
		};
		const { user } = renderAt(`/roles/${id}`);

		expect(
			screen.getByText(
				"Takes effect when secret decryption is enforced.",
			),
		).toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "People" }));
		expect(await screen.findByText("People panel")).toBeInTheDocument();
	});

	it("says nothing about enforcement for roles that are enforced", () => {
		state.role = {
			...customRole,
			id: "operator",
			name: "Platform Operator",
			is_builtin: true,
			is_base: false,
			consumer_counts: null,
		};
		renderAt("/roles/operator");

		expect(screen.queryByText(/Takes effect when/)).not.toBeInTheDocument();
	});

	it("lists no people for base roles or callers without role-assignment access", () => {
		state.role = {
			...customRole,
			id: "user-role",
			name: "User",
			is_builtin: true,
			is_base: true,
			consumer_counts: null,
		};
		const { unmount } = renderAt("/roles/user-role");
		expect(
			screen.queryByRole("tab", { name: "People" }),
		).not.toBeInTheDocument();
		unmount();

		state.canViewPeople = false;
		state.role = {
			...customRole,
			id: "operator",
			name: "Platform Operator",
			is_builtin: true,
			is_base: false,
			consumer_counts: null,
		};
		renderAt("/roles/operator");
		expect(
			screen.queryByRole("tab", { name: "People" }),
		).not.toBeInTheDocument();
	});
});
