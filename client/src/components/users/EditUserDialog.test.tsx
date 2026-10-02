/**
 * Component tests for EditUserDialog.
 *
 * Covers:
 * - pre-fills fields and sends only changed fields
 * - "editing your own account" limits edits to the display name
 * - each field is enabled by the permission that decides it, and disabled
 *   fields say why (support vs user lifecycle)
 * - protected users are read-only to everyone but a Platform Admin
 * - the Roles & access tab appears only with roleassignments.read
 *
 * The Combobox is stubbed to a <select> for the same reason as
 * CreateUserDialog.test.tsx — driving Radix popovers in happy-dom is slow
 * and brittle. The Roles & access panel has its own tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import {
	canAnywhere,
	canAt,
	type AuthorizationGrant,
	type AuthorizationSummary,
	type AuthorizationTarget,
} from "@/lib/authorization";

const mockUpdateMutate = vi.fn();
const mockOrganizations = vi.fn();
const mockAuth = vi.fn();
const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
}));

vi.mock("@/hooks/useUsers", () => ({
	useUpdateUser: () => ({
		mutateAsync: mockUpdateMutate,
		isPending: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockOrganizations(),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: authz.summary,
		isPlatformAdmin: authz.summary?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
	}),
}));

vi.mock("./UserRoleAssignmentsPanel", () => ({
	UserRoleAssignmentsPanel: () => <p>Roles panel</p>,
}));

vi.mock("@/components/ui/combobox", () => ({
	Combobox: ({
		id,
		value,
		onValueChange,
		options,
		disabled,
	}: {
		id?: string;
		value?: string;
		onValueChange?: (v: string) => void;
		options: { value: string; label: string }[];
		disabled?: boolean;
	}) => (
		<select
			aria-label={id}
			id={id}
			value={value ?? ""}
			disabled={disabled}
			onChange={(e) => onValueChange?.(e.target.value)}
		>
			<option value="">(none)</option>
			{options.map((opt) => (
				<option key={opt.value} value={opt.value}>
					{opt.label}
				</option>
			))}
		</select>
	),
}));

import { EditUserDialog } from "./EditUserDialog";

type User = Parameters<typeof EditUserDialog>[0]["user"];

const PROVIDER = "org-provider";

function makeUser(
	overrides: Partial<NonNullable<User>> = {},
): NonNullable<User> {
	return {
		id: "u-1",
		email: "alice@example.com",
		name: "Alice",
		is_active: true,
		is_superuser: false,
		is_external: false,
		is_protected: false,
		organization_id: "org-1",
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		last_login: null,
		...overrides,
	} as NonNullable<User>;
}

function summary(
	isPlatformAdmin: boolean,
	grants: AuthorizationGrant[] = [],
): AuthorizationSummary {
	return {
		is_platform_admin: isPlatformAdmin,
		home_organization_id: PROVIDER,
		provider_organization_id: PROVIDER,
		base_role: { id: "base", name: "Base" },
		grants,
	};
}

function managed(...permissions: string[]): AuthorizationGrant[] {
	return permissions.map((permission) => ({
		permission,
		boundary: { kind: "managed_organizations", organization_id: null },
	}));
}

const operator = () =>
	summary(
		false,
		managed(
			"users.read",
			"users.readwrite",
			"organizations.read",
			"roleassignments.read",
		),
	);

beforeEach(() => {
	mockUpdateMutate.mockReset();
	mockUpdateMutate.mockResolvedValue({});
	mockOrganizations.mockReturnValue({
		data: [
			{
				id: "org-1",
				name: "Acme",
				domain: "acme.com",
				is_provider: false,
			},
			{ id: "org-2", name: "Globex", domain: null, is_provider: false },
			{ id: PROVIDER, name: "Provider", domain: null, is_provider: true },
		],
		isLoading: false,
	});
	mockAuth.mockReturnValue({
		user: { id: "other-user", email: "admin@example.com" },
	});
	authz.summary = summary(true);
});

describe("EditUserDialog", () => {
	it("pre-fills the display name from the user prop", () => {
		renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={vi.fn()}
			/>,
		);

		expect(screen.getByLabelText(/display name/i)).toHaveValue("Alice");
		expect(screen.getByLabelText(/email address/i)).toBeDisabled();
		expect(
			screen.getByRole("button", { name: /close dialog/i }),
		).toBeInTheDocument();
	});

	it("limits self edits to the display name", async () => {
		const account = makeUser({ organization_id: null });
		mockAuth.mockReturnValue({
			user: { id: account.id, email: account.email },
		});
		const { user } = renderWithProviders(
			<EditUserDialog user={account} open onOpenChange={vi.fn()} />,
		);
		expect(
			screen.getByText(/editing your own account/i),
		).toBeInTheDocument();
		expect(screen.getByLabelText("Account Status")).toBeDisabled();

		await user.clear(screen.getByLabelText(/display name/i));
		await user.type(
			screen.getByLabelText(/display name/i),
			"Updated Self Name",
		);
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0][0].body).toEqual({
			name: "Updated Self Name",
			organization_id: null,
			is_active: null,
			is_external: null,
		});
	});

	it("submits only the name delta when just the name is changed", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={onOpenChange}
			/>,
		);

		const nameInput = screen.getByLabelText(/display name/i);
		await user.clear(nameInput);
		await user.type(nameInput, "Alice Updated");

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		const call = mockUpdateMutate.mock.calls[0]![0];
		expect(call.params).toEqual({ path: { user_id: "u-1" } });
		expect(call.body).toEqual({
			name: "Alice Updated",
			is_active: null,
			organization_id: null,
			is_external: null,
		});
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("does not call update when nothing has changed", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={onOpenChange}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		expect(mockUpdateMutate).not.toHaveBeenCalled();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("lets user support change support fields but not lifecycle fields", async () => {
		authz.summary = operator();
		const { user } = renderWithProviders(
			<EditUserDialog user={makeUser()} open onOpenChange={vi.fn()} />,
		);

		expect(screen.getByLabelText(/display name/i)).toBeEnabled();
		expect(screen.getByLabelText("Account Status")).toBeEnabled();
		expect(screen.getByLabelText("Organization")).toBeDisabled();
		expect(screen.getByLabelText("Organization")).toHaveValue("Acme");
		expect(screen.getByLabelText("External user")).toBeDisabled();
		expect(
			screen.getAllByText(
				/only people who can create, move, or delete users/i,
			),
		).toHaveLength(2);

		await user.click(screen.getByLabelText("Account Status"));
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0][0].body).toEqual({
			name: null,
			is_active: false,
			organization_id: null,
			is_external: null,
		});
	});

	it("offers move destinations only where the caller manages user lifecycle", () => {
		authz.summary = summary(false, [
			...managed("users.read", "organizations.read"),
			{
				permission: "users.lifecycle.readwrite",
				boundary: { kind: "organization", organization_id: "org-1" },
			},
		]);
		renderWithProviders(
			<EditUserDialog user={makeUser()} open onOpenChange={vi.fn()} />,
		);

		const options = Array.from(
			screen.getByLabelText("organization").querySelectorAll("option"),
		).map((option) => option.textContent);
		expect(options).toEqual(["(none)", "Acme"]);
		expect(screen.getByLabelText("Account Status")).toBeDisabled();
		expect(
			screen.getAllByText(/only people who can manage users/i),
		).toHaveLength(2);
	});

	it("makes a protected user read-only for anyone but a Platform Admin", () => {
		authz.summary = operator();
		renderWithProviders(
			<EditUserDialog
				user={makeUser({ is_protected: true })}
				open
				onOpenChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: /user details/i }),
		).toBeInTheDocument();
		expect(screen.getByText("Protected account")).toBeInTheDocument();
		expect(
			screen.getByText(/only a platform admin can change them/i),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/display name/i)).toBeDisabled();
		expect(screen.getByLabelText("Account Status")).toBeDisabled();
		expect(
			screen.queryByRole("button", { name: /save changes/i }),
		).not.toBeInTheDocument();
	});

	it("lets a Platform Admin edit a protected user", () => {
		renderWithProviders(
			<EditUserDialog
				user={makeUser({ is_protected: true })}
				open
				onOpenChange={vi.fn()}
			/>,
		);

		expect(screen.getByText("Protected account")).toBeInTheDocument();
		expect(
			screen.getByText(
				"This person holds privileged access. Only Platform Admins can change their profile, sign-in, or roles.",
			),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/display name/i)).toBeEnabled();
		expect(
			screen.getByRole("button", { name: /save changes/i }),
		).toBeEnabled();
	});

	it("shows Roles & access only to callers who can view role assignments", async () => {
		const { user, unmount } = renderWithProviders(
			<EditUserDialog user={makeUser()} open onOpenChange={vi.fn()} />,
		);
		await user.click(screen.getByRole("tab", { name: "Roles & access" }));
		expect(screen.getByText("Roles panel")).toBeVisible();
		unmount();

		authz.summary = summary(
			false,
			managed("users.read", "users.readwrite"),
		);
		renderWithProviders(
			<EditUserDialog user={makeUser()} open onOpenChange={vi.fn()} />,
		);
		expect(
			screen.queryByRole("tab", { name: "Roles & access" }),
		).not.toBeInTheDocument();
	});
});

it("keeps a pending save open and preserves the draft after failure", async () => {
	let rejectSave!: (error: Error) => void;
	mockUpdateMutate.mockImplementationOnce(
		() =>
			new Promise((_resolve, reject) => {
				rejectSave = reject;
			}),
	);
	const onOpenChange = vi.fn();
	const { user } = renderWithProviders(
		<EditUserDialog user={makeUser()} open onOpenChange={onOpenChange} />,
	);
	await user.clear(screen.getByLabelText(/display name/i));
	await user.type(screen.getByLabelText(/display name/i), "Preserved draft");
	await user.click(screen.getByRole("button", { name: "Save Changes" }));
	await user.keyboard("{Escape}");
	expect(onOpenChange).not.toHaveBeenCalled();
	expect(screen.getByRole("button", { name: "Close dialog" })).toBeDisabled();
	expect(
		screen.getByLabelText(/display name/i).closest("[inert]"),
	).not.toBeNull();
	rejectSave(new Error("Synthetic user save failure"));
	const error = await screen.findByRole("alert");
	await waitFor(() => expect(error).toHaveFocus());
	expect(screen.getByLabelText(/display name/i)).toHaveValue(
		"Preserved draft",
	);
	expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();
});
