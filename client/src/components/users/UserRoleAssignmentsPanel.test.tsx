import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import {
	canAnywhere,
	canAt,
	type AuthorizationGrant,
	type AuthorizationSummary,
	type AuthorizationTarget,
} from "@/lib/authorization";
import type { components } from "@/lib/v1";

type Assignments = components["schemas"]["UserRoleAssignmentsResponse"];
type User = components["schemas"]["UserPublic"];

const PROVIDER = "00000000-0000-0000-0000-000000000002";
const ADMIN_ROLE = "00000000-0000-0000-0000-000000000005";
const USER_ROLE = "00000000-0000-0000-0000-000000000006";
const OPERATOR_ROLE = "00000000-0000-0000-0000-000000000007";
const SECRETS_ROLE = "00000000-0000-0000-0000-000000000008";
const SUPPORT_ROLE = "custom-support";

const state = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
	assignments: undefined as Assignments | undefined,
	mutateAsync: vi.fn(),
}));

vi.mock("@/hooks/useUsers", () => ({
	useUserRoleAssignments: () => ({
		data: state.assignments,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useReplaceUserRoleAssignments: () => ({
		mutateAsync: state.mutateAsync,
		isPending: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [
			{ id: PROVIDER, name: "Provider", is_provider: true },
			{ id: "org-a", name: "Contoso", is_provider: false },
			{ id: "org-b", name: "Fabrikam", is_provider: false },
		],
	}),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: state.summary,
		isPlatformAdmin: state.summary?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(state.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(state.summary, permission),
	}),
}));

vi.mock("@/components/ui/combobox", () => ({
	Combobox: ({
		id,
		value,
		onValueChange,
		options,
	}: {
		id?: string;
		value?: string;
		onValueChange?: (v: string) => void;
		options: { value: string; label: string }[];
	}) => (
		<select
			aria-label={id}
			value={value}
			onChange={(e) => onValueChange?.(e.target.value)}
		>
			{options.map((opt) => (
				<option key={opt.value} value={opt.value}>
					{opt.label}
				</option>
			))}
		</select>
	),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { UserRoleAssignmentsPanel } from "./UserRoleAssignmentsPanel";

function summary(
	isPlatformAdmin: boolean,
	grants: AuthorizationGrant[] = [],
): AuthorizationSummary {
	return {
		is_platform_admin: isPlatformAdmin,
		home_organization_id: PROVIDER,
		provider_organization_id: PROVIDER,
		base_role: { id: USER_ROLE, name: "User" },
		grants,
	};
}

const operatorCaller = () =>
	summary(
		false,
		["users.read", "roleassignments.read", "roleassignments.readwrite"].map(
			(permission) => ({
				permission,
				boundary: {
					kind: "managed_organizations" as const,
					organization_id: null,
				},
			}),
		),
	);

function makeUser(overrides: Partial<User> = {}): User {
	return {
		id: "u-1",
		email: "pat@example.com",
		name: "Pat",
		is_active: true,
		is_superuser: false,
		is_external: false,
		is_protected: false,
		organization_id: PROVIDER,
		...overrides,
	} as User;
}

const supportRole = {
	id: SUPPORT_ROLE,
	name: "Help desk",
	description: "Shares help desk forms",
	is_builtin: false,
	permissions: [],
	can_be_base: false,
	can_be_additional: true,
	boundary_kinds: ["organization" as const],
	provider_organization_allowed: true,
};

function adminView(): Assignments {
	return {
		base_role: { id: USER_ROLE, name: "User", is_builtin: true },
		additional: [],
		is_protected: false,
		assignable_roles: [
			{
				id: USER_ROLE,
				name: "User",
				is_builtin: true,
				permissions: ["agents.read", "agentruns.read", "forms.read"],
				can_be_base: true,
				can_be_additional: false,
				boundary_kinds: [],
				provider_organization_allowed: true,
			},
			{
				id: "billing-forms",
				name: "Billing Forms",
				is_builtin: false,
				permissions: [],
				can_be_base: true,
				can_be_additional: true,
				boundary_kinds: ["organization"],
				provider_organization_allowed: true,
			},
			{
				id: ADMIN_ROLE,
				name: "Platform Admin",
				description: "Full platform administration.",
				is_builtin: true,
				permissions: [],
				can_be_base: false,
				can_be_additional: true,
				boundary_kinds: ["platform"],
				provider_organization_allowed: true,
			},
			{
				id: OPERATOR_ROLE,
				name: "Platform Operator",
				description:
					"Support for customer organizations: view organizations and users",
				is_builtin: true,
				permissions: ["users.read"],
				can_be_base: false,
				can_be_additional: true,
				boundary_kinds: ["organization", "managed_organizations"],
				provider_organization_allowed: false,
			},
			{
				id: SECRETS_ROLE,
				name: "Secrets Reader",
				is_builtin: true,
				permissions: ["secrets.read"],
				can_be_base: false,
				can_be_additional: true,
				boundary_kinds: [],
				provider_organization_allowed: true,
				fixed_boundaries: [
					{ kind: "platform", organization_id: null },
					{ kind: "managed_organizations", organization_id: null },
					{ kind: "organization", organization_id: PROVIDER },
				],
			},
			{
				...supportRole,
				boundary_kinds: [
					"organization",
					"managed_organizations",
					"platform",
				],
			},
		],
	};
}

function render(user = makeUser(), isSelf = false) {
	return renderWithProviders(
		<UserRoleAssignmentsPanel user={user} isSelf={isSelf} />,
	);
}

beforeEach(() => {
	state.summary = summary(true);
	state.assignments = adminView();
	state.mutateAsync.mockReset();
	state.mutateAsync.mockImplementation(async () => state.assignments);
});

describe("UserRoleAssignmentsPanel", () => {
	it("assigns Platform Operator for all customer organizations in one save", async () => {
		const { user } = render();
		const save = screen.getByRole("button", { name: "Save roles" });
		expect(save).toBeDisabled();
		expect(screen.getByText("No additional roles.")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Platform Operator/ }),
		);

		const places = screen.getByRole("list", {
			name: "Where Platform Operator applies",
		});
		expect(
			within(places).getByText("In all customer organizations"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Support for customer organizations/),
		).toBeInTheDocument();

		await user.click(save);
		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0]).toEqual({
			params: { path: { user_id: "u-1" } },
			body: {
				base_role_id: USER_ROLE,
				additional: [
					{
						role_id: OPERATOR_ROLE,
						boundaries: [
							{
								kind: "managed_organizations",
								organization_id: null,
							},
						],
					},
				],
			},
		});
	});

	it("assigns Secrets Reader with its fixed places and no picker", async () => {
		const { user } = render();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Secrets Reader/ }),
		);

		expect(screen.getByText("Applies everywhere.")).toBeInTheDocument();
		expect(
			screen.queryByRole("list", {
				name: "Where Secrets Reader applies",
			}),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", {
				name: "Add where Secrets Reader applies",
			}),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Save roles" }));
		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0].body.additional).toEqual([
			{
				role_id: SECRETS_ROLE,
				boundaries: [
					{ kind: "platform", organization_id: null },
					{ kind: "managed_organizations", organization_id: null },
					{ kind: "organization", organization_id: PROVIDER },
				],
			},
		]);
	});

	it("offers Operator only its allowed places, never the provider org", async () => {
		const { user } = render();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Platform Operator/ }),
		);
		await user.click(
			screen.getByRole("button", {
				name: "Add where Platform Operator applies",
			}),
		);

		const options = within(await screen.findByRole("listbox"))
			.getAllByRole("option")
			.map((option) => option.textContent);
		expect(options).toEqual(["Contoso", "Fabrikam"]);
	});

	it("explains how to make someone outside the provider org a Platform Admin", () => {
		state.assignments = {
			...adminView(),
			assignable_roles: adminView().assignable_roles.filter(
				(role) => role.id !== ADMIN_ROLE,
			),
		};
		render(makeUser({ organization_id: "org-a" }));

		expect(
			screen.getByText(/first move them to the provider organization/i),
		).toBeInTheDocument();
	});

	it("never offers Platform Admin as a base role", () => {
		render();

		expect(
			within(screen.getByLabelText("base-role")).queryByRole("option", {
				name: "Platform Admin",
			}),
		).not.toBeInTheDocument();
	});

	it("warns before adding Platform Admin, which applies platform-wide with no choice", async () => {
		const { user } = render();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Platform Admin/ }),
		);

		expect(
			screen.getByText(/unrestricted access to every organization/i),
		).toBeInTheDocument();
		expect(
			within(
				screen.getByRole("list", {
					name: "Where Platform Admin applies",
				}),
			).getByText("Platform-wide"),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", {
				name: "Add where Platform Admin applies",
			}),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Save roles" }));
		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0].body).toEqual({
			base_role_id: USER_ROLE,
			additional: [
				{
					role_id: ADMIN_ROLE,
					boundaries: [{ kind: "platform", organization_id: null }],
				},
			],
		});
	});

	it("warns before removing Platform Admin", async () => {
		state.assignments = {
			...adminView(),
			additional: [
				{
					role_id: ADMIN_ROLE,
					name: "Platform Admin",
					is_builtin: true,
					permissions: [],
					boundaries: [{ kind: "platform", organization_id: null }],
				},
			],
		};
		const { user } = render(makeUser({ is_superuser: true }));

		await user.click(
			screen.getByRole("button", { name: "Remove Platform Admin" }),
		);

		expect(
			screen.getByText(/lose access to other organizations/i),
		).toBeInTheDocument();
	});

	it("keeps Platform Admin on a Global user and says why", () => {
		state.assignments = {
			...adminView(),
			additional: [
				{
					role_id: ADMIN_ROLE,
					name: "Platform Admin",
					is_builtin: true,
					permissions: [],
					boundaries: [{ kind: "platform", organization_id: null }],
				},
			],
			assignable_roles: adminView().assignable_roles.filter(
				(role) => role.id !== ADMIN_ROLE,
			),
		};
		render(makeUser({ is_superuser: true, organization_id: null }));

		expect(
			screen.queryByRole("button", { name: "Remove Platform Admin" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByText(/before removing Platform Admin/i),
		).toBeInTheDocument();
	});

	it("spells out what a custom base role replaces", async () => {
		const { user } = render(
			makeUser({ name: "Alice", organization_id: "org-a" }),
		);
		await user.selectOptions(
			screen.getByLabelText("base-role"),
			"billing-forms",
		);

		expect(
			screen.getByText(
				"Billing Forms replaces User as Alice's base role. In Contoso, they'll have only Billing Forms's permissions (none) instead of User's (view agents, view agent runs, view forms).",
			),
		).toBeInTheDocument();

		await user.selectOptions(screen.getByLabelText("base-role"), USER_ROLE);
		expect(screen.queryByText(/replaces User/)).not.toBeInTheDocument();
	});

	it("lets a delegate grant what the server allows, only where they reach", async () => {
		state.summary = operatorCaller();
		state.assignments = {
			base_role: { id: USER_ROLE, name: "User", is_builtin: true },
			additional: [
				{
					role_id: OPERATOR_ROLE,
					name: "Platform Operator",
					is_builtin: true,
					permissions: ["users.read"],
					boundaries: [
						{
							kind: "managed_organizations",
							organization_id: null,
						},
					],
				},
			],
			is_protected: false,
			assignable_roles: [supportRole],
		};
		const { user } = render(makeUser({ organization_id: "org-a" }));

		expect(screen.queryByLabelText("base-role")).not.toBeInTheDocument();
		expect(
			screen.getByText("You can't change this person's base role."),
		).toBeInTheDocument();
		expect(
			screen.getByText(/You can't change this role. Saving keeps it/),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Remove Platform Operator" }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Help desk/ }),
		);
		expect(
			within(
				screen.getByRole("list", { name: "Where Help desk applies" }),
			).getByText("In Contoso"),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Add where Help desk applies" }),
		);
		const options = within(await screen.findByRole("listbox"))
			.getAllByRole("option")
			.map((option) => option.textContent);
		expect(options).toEqual(["Fabrikam"]);
	});

	it("lets an admin remove an Operator role the user can no longer be given", async () => {
		state.assignments = {
			...adminView(),
			additional: [
				{
					role_id: OPERATOR_ROLE,
					name: "Platform Operator",
					is_builtin: true,
					permissions: ["users.read"],
					boundaries: [
						{
							kind: "managed_organizations",
							organization_id: null,
						},
					],
				},
			],
			assignable_roles: adminView().assignable_roles.map((role) =>
				role.id === OPERATOR_ROLE
					? { ...role, can_be_additional: false }
					: role,
			),
		};
		const { user } = render(makeUser({ organization_id: "org-a" }));

		expect(
			screen.getByText(/can't be given this role any more/),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", {
				name: "Add where Platform Operator applies",
			}),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		expect(
			screen.queryByRole("option", { name: /Platform Operator/ }),
		).not.toBeInTheDocument();
		await user.keyboard("{Escape}");

		await user.click(
			screen.getByRole("button", { name: "Remove Platform Operator" }),
		);
		await user.click(screen.getByRole("button", { name: "Save roles" }));
		await waitFor(() => expect(state.mutateAsync).toHaveBeenCalled());
		expect(state.mutateAsync.mock.calls[0][0].body.additional).toEqual([]);
	});

	it("is read-only without permission to assign roles", () => {
		state.summary = summary(false, [
			{
				permission: "roleassignments.read",
				boundary: {
					kind: "managed_organizations",
					organization_id: null,
				},
			},
		]);
		render(makeUser({ organization_id: "org-a" }));

		expect(
			screen.getByText("You can view these roles but not change them."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Add role" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Save roles" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Close" }),
		).not.toBeInTheDocument();
	});

	it("never lets people change their own roles", () => {
		render(makeUser(), true);

		expect(
			screen.getByText("You can't change your own roles."),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Save roles" }),
		).not.toBeInTheDocument();
	});

	it("shows the server's refusal inline and keeps the draft", async () => {
		state.mutateAsync.mockRejectedValue({
			detail: "Platform Operator applies only at managed organizations or at customer organizations",
		});
		const { user } = render();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Platform Operator/ }),
		);
		await user.click(screen.getByRole("button", { name: "Save roles" }));

		expect(
			await screen.findByText(/applies only at managed organizations/),
		).toBeInTheDocument();
		expect(
			screen.getByRole("list", {
				name: "Where Platform Operator applies",
			}),
		).toBeInTheDocument();
	});

	it("discards unsaved changes", async () => {
		const { user } = render();
		await user.click(screen.getByRole("button", { name: "Add role" }));
		await user.click(
			await screen.findByRole("option", { name: /Help desk/ }),
		);
		await user.click(
			screen.getByRole("button", { name: "Discard changes" }),
		);

		expect(screen.getByText("No additional roles.")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Save roles" }),
		).toBeDisabled();
	});
});
