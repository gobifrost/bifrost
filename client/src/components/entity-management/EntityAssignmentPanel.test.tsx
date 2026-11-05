import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { EntityAssignmentPanel } from "./EntityAssignmentPanel";
import type { EntityWithScope, Organization, Role } from "./types";

const { mockUseAuthorization } = vi.hoisted(() => ({
	mockUseAuthorization: vi.fn(),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		onChange,
		disabled,
	}: {
		onChange: (value: string | null) => void;
		disabled?: boolean;
	}) => (
		<button
			type="button"
			disabled={disabled}
			onClick={() => onChange("org-1")}
		>
			Northwind
		</button>
	),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

mockUseAuthorization.mockReturnValue({ authorization: undefined });

it("reviews scope and additive access changes, then applies both to the frozen selection", async () => {
	const user = userEvent.setup();
	const onOrganization = vi.fn().mockResolvedValue(undefined);
	const onAccess = vi.fn().mockResolvedValue(undefined);
	const props = {
		entities: [
			entity(
				"workflow:a",
				"Create service request",
				"workflow",
				null,
				"private",
				[],
			),
			entity(
				"agent:b",
				"Support assistant",
				"agent",
				"org-1",
				"role_based",
				["role-2"],
			),
		],
		organizations,
		roles,
		disabled: false,
		onOrganization,
		onAccess,
		selectedIds: new Set(["workflow:a", "agent:b"]),
	};
	const { rerender } = render(<EntityAssignmentPanel {...props} />);

	expect(
		screen.queryByRole("button", { name: "Northwind" }),
	).not.toBeInTheDocument();
	expect(
		screen.queryByRole("combobox", { name: "Role to add" }),
	).not.toBeInTheDocument();

	await user.click(
		screen.getByRole("combobox", { name: "Organization change mode" }),
	);
	await user.click(screen.getByRole("option", { name: "Set scope" }));
	await user.click(screen.getByRole("button", { name: "Northwind" }));
	await user.click(screen.getByRole("combobox", { name: "Roles change" }));
	await user.click(screen.getByRole("option", { name: "Add role" }));
	expect(
		screen.getByRole("combobox", { name: "Role to add" }),
	).toHaveTextContent("Select role...");
	await user.click(screen.getByRole("combobox", { name: "Role to add" }));
	await user.click(screen.getByRole("option", { name: "Service Desk" }));

	expect(
		screen.getByText("Create service request").closest("li"),
	).toHaveTextContent(/Scope:GlobalNorthwind/);
	expect(
		screen.getByText("Create service request").closest("li"),
	).toHaveTextContent(/Access:PrivateRestricted to roles/);
	expect(
		screen.getByText("Create service request").closest("li"),
	).toHaveTextContent(/Roles:No rolesService Desk/);
	expect(
		screen.getByText("Support assistant").closest("li"),
	).not.toHaveTextContent("Scope:");
	expect(
		screen.getByText("Support assistant").closest("li"),
	).not.toHaveTextContent("Access:");
	expect(
		screen.getByText("Support assistant").closest("li"),
	).toHaveTextContent(/Roles:Existing RoleExisting Role, Service Desk/);

	const apply = screen.getByRole("button", { name: "Apply changes" });
	await user.click(apply);
	rerender(
		<EntityAssignmentPanel
			{...props}
			selectedIds={new Set(["workflow:other"])}
		/>,
	);

	await waitFor(() =>
		expect(onOrganization).toHaveBeenCalledWith(["workflow:a"], "org-1"),
	);
	expect(onAccess).toHaveBeenCalledWith(["workflow:a", "agent:b"], {
		accessLevel: "role_based",
		addRoleId: "role-1",
	});
});

it("does not apply access or reset review state when the scope phase fails", async () => {
	const user = userEvent.setup();
	const onOrganization = vi.fn().mockRejectedValue(new Error("Scope failed"));
	const onAccess = vi.fn().mockResolvedValue(undefined);
	render(
		<EntityAssignmentPanel
			entities={[
				entity(
					"workflow:a",
					"Create service request",
					"workflow",
					null,
					"authenticated",
					[],
				),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={onOrganization}
			onAccess={onAccess}
			selectedIds={new Set(["workflow:a"])}
		/>,
	);

	await user.click(
		screen.getByRole("combobox", { name: "Organization change mode" }),
	);
	await user.click(screen.getByRole("option", { name: "Set scope" }));
	await user.click(screen.getByRole("button", { name: "Northwind" }));
	await user.click(
		screen.getByRole("combobox", { name: "Access level change" }),
	);
	await user.click(
		screen.getByRole("option", {
			name: /Everyone except external users/,
		}),
	);
	await user.click(screen.getByRole("button", { name: "Apply changes" }));

	await waitFor(() =>
		expect(screen.getByRole("alert")).toHaveTextContent("Scope failed"),
	);
	expect(onOrganization).toHaveBeenCalledWith(["workflow:a"], "org-1");
	expect(onAccess).not.toHaveBeenCalled();
	expect(
		screen.getByText("Create service request").closest("li"),
	).toHaveTextContent(/Scope:GlobalNorthwind/);
	expect(screen.getByRole("button", { name: "Apply changes" })).toBeEnabled();
});

it("requires a selection and a proposed change", async () => {
	const user = userEvent.setup();
	const onAccess = vi.fn().mockResolvedValue(undefined);
	const { rerender } = render(
		<EntityAssignmentPanel
			entities={[
				entity("workflow:a", "Alpha", "workflow", null, "private", []),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={vi.fn()}
			onAccess={onAccess}
			selectedIds={new Set()}
		/>,
	);

	expect(
		screen.getByRole("button", { name: "Apply changes" }),
	).toBeDisabled();
	rerender(
		<EntityAssignmentPanel
			entities={[
				entity("workflow:a", "Alpha", "workflow", null, "private", []),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={vi.fn()}
			onAccess={onAccess}
			selectedIds={new Set(["workflow:a"])}
		/>,
	);
	expect(
		screen.getByRole("button", { name: "Apply changes" }),
	).toBeDisabled();
	await user.click(
		screen.getByRole("combobox", { name: "Access level change" }),
	);
	await user.click(
		screen.getByRole("option", {
			name: /Everyone except external users/,
		}),
	);
	await user.click(screen.getByRole("button", { name: "Apply changes" }));
	await waitFor(() =>
		expect(onAccess).toHaveBeenCalledWith(["workflow:a"], {
			accessLevel: "authenticated",
		}),
	);
});

it("does not apply when the proposed access change is already effective", async () => {
	const user = userEvent.setup();
	const onAccess = vi.fn().mockResolvedValue(undefined);
	render(
		<EntityAssignmentPanel
			entities={[
				entity(
					"workflow:a",
					"Alpha",
					"workflow",
					null,
					"authenticated",
					[],
				),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={vi.fn()}
			onAccess={onAccess}
			selectedIds={new Set(["workflow:a"])}
		/>,
	);

	await user.click(
		screen.getByRole("combobox", { name: "Access level change" }),
	);
	await user.click(
		screen.getByRole("option", {
			name: /Everyone except external users/,
		}),
	);

	expect(
		screen.getByText("No selected resources would change."),
	).toBeVisible();
	expect(screen.queryByText("Review changes")).not.toBeInTheDocument();
	expect(
		screen.getByRole("button", { name: "Apply changes" }),
	).toBeDisabled();
});

it("does not treat an incomplete role add as an access change", async () => {
	const user = userEvent.setup();
	const onAccess = vi.fn().mockResolvedValue(undefined);
	render(
		<EntityAssignmentPanel
			entities={[
				entity("workflow:a", "Alpha", "workflow", null, "private", []),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={vi.fn()}
			onAccess={onAccess}
			selectedIds={new Set(["workflow:a"])}
		/>,
	);

	await user.click(screen.getByRole("combobox", { name: "Roles change" }));
	await user.click(screen.getByRole("option", { name: "Add role" }));

	expect(screen.queryByText("Review changes")).not.toBeInTheDocument();
	expect(
		screen.getByText("No selected resources would change."),
	).toBeVisible();
	expect(
		screen.getByRole("button", { name: "Apply changes" }),
	).toBeDisabled();
	expect(onAccess).not.toHaveBeenCalled();
});

it("lets explicit access level win over implicit role-based role changes", async () => {
	const user = userEvent.setup();
	const onAccess = vi.fn().mockResolvedValue(undefined);
	render(
		<EntityAssignmentPanel
			entities={[
				entity("workflow:a", "Alpha", "workflow", null, "private", [
					"role-2",
				]),
			]}
			organizations={organizations}
			roles={roles}
			disabled={false}
			onOrganization={vi.fn()}
			onAccess={onAccess}
			selectedIds={new Set(["workflow:a"])}
		/>,
	);

	await user.click(
		screen.getByRole("combobox", { name: "Access level change" }),
	);
	await user.click(
		screen.getByRole("option", {
			name: /Everyone except external users/,
		}),
	);
	await user.click(screen.getByRole("combobox", { name: "Roles change" }));
	await user.click(screen.getByRole("option", { name: "Clear roles" }));

	expect(screen.getByText("Alpha").closest("li")).toHaveTextContent(
		/Access:PrivateEveryone except external users/,
	);
	expect(screen.getByText("Alpha").closest("li")).toHaveTextContent(
		/Roles:Existing RoleNo roles/,
	);

	await user.click(screen.getByRole("button", { name: "Apply changes" }));

	await waitFor(() =>
		expect(onAccess).toHaveBeenCalledWith(["workflow:a"], {
			accessLevel: "authenticated",
			clearRoles: true,
		}),
	);
});

const organizations = [
	{
		id: "org-1",
		name: "Northwind",
		is_active: true,
		is_provider: false,
		created_at: null,
		created_by: "test",
		updated_at: null,
	},
] as Organization[];

const roles = [
	{
		id: "role-1",
		name: "Service Desk",
		created_by: "test",
		created_at: null,
		updated_at: null,
	},
	{
		id: "role-2",
		name: "Existing Role",
		created_by: "test",
		created_at: null,
		updated_at: null,
	},
] as Role[];

function entity(
	key: string,
	name: string,
	entityType: EntityWithScope["entityType"],
	organizationId: string | null,
	accessLevel: string | null,
	roleIds: string[],
): EntityWithScope {
	return {
		key,
		id: key.slice(key.indexOf(":") + 1),
		name,
		entityType,
		organizationId,
		accessLevel,
		roleIds,
		createdAt: "2026-01-01T00:00:00Z",
		usedByCount: 0,
		original: {},
	} as EntityWithScope;
}
