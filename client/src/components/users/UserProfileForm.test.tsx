/**
 * Component tests for UserProfileForm.
 *
 * Covers:
 * - pre-fills fields and sends only changed fields
 * - "editing your own account" limits edits to the display name
 * - each field is enabled by the permission that decides it, and disabled
 *   fields say why (support vs user lifecycle)
 * - protected users are read-only to everyone but a Platform Admin
 * - a pending save reports busy and a failure keeps the draft
 * - the dialog variant offers Cancel; the page variant only saves
 *
 * The Combobox is stubbed to a <select> for the same reason as
 * CreateUserDialog.test.tsx — driving Radix popovers in happy-dom is slow
 * and brittle.
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

import { UserProfileForm } from "./UserProfileForm";

type User = Parameters<typeof UserProfileForm>[0]["user"];

const PROVIDER = "org-provider";

function makeUser(overrides: Partial<User> = {}): User {
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
	} as User;
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

describe("UserProfileForm", () => {
	it("pre-fills the display name and keeps the email fixed", () => {
		renderWithProviders(
			<UserProfileForm user={makeUser()} variant="page" />,
		);

		expect(screen.getByLabelText(/display name/i)).toHaveValue("Alice");
		expect(screen.getByLabelText(/email address/i)).toBeDisabled();
	});

	it("limits self edits to the display name", async () => {
		const account = makeUser({ organization_id: null });
		mockAuth.mockReturnValue({
			user: { id: account.id, email: account.email },
		});
		const { user } = renderWithProviders(
			<UserProfileForm user={account} variant="page" />,
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

	it("submits only the name delta and reports it is done", async () => {
		const onDone = vi.fn();
		const { user } = renderWithProviders(
			<UserProfileForm
				user={makeUser()}
				variant="page"
				onDone={onDone}
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
		expect(onDone).toHaveBeenCalledOnce();
	});

	it("does not call update when nothing has changed", async () => {
		const onDone = vi.fn();
		const { user } = renderWithProviders(
			<UserProfileForm
				user={makeUser()}
				variant="page"
				onDone={onDone}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		expect(mockUpdateMutate).not.toHaveBeenCalled();
		expect(onDone).toHaveBeenCalledOnce();
	});

	it("lets user support change support fields but not lifecycle fields", async () => {
		authz.summary = operator();
		const { user } = renderWithProviders(
			<UserProfileForm user={makeUser()} variant="page" />,
		);

		expect(screen.getByLabelText(/display name/i)).toBeEnabled();
		expect(screen.getByLabelText("Account Status")).toBeEnabled();
		expect(screen.getByLabelText("Organization")).toBeDisabled();
		expect(screen.getByLabelText("Organization")).toHaveValue("Acme");
		expect(screen.getByLabelText("External User")).toBeDisabled();
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
				permission: "userlifecycle.readwrite",
				boundary: { kind: "organization", organization_id: "org-1" },
			},
		]);
		renderWithProviders(
			<UserProfileForm user={makeUser()} variant="page" />,
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
			<UserProfileForm
				user={makeUser({ is_protected: true })}
				variant="page"
			/>,
		);

		expect(screen.getByLabelText(/display name/i)).toBeDisabled();
		expect(screen.getByLabelText("Account Status")).toBeDisabled();
		expect(
			screen.queryByRole("button", { name: /save changes/i }),
		).not.toBeInTheDocument();
	});

	it("lets a Platform Admin edit a protected user", () => {
		renderWithProviders(
			<UserProfileForm
				user={makeUser({ is_protected: true })}
				variant="page"
			/>,
		);

		expect(screen.getByLabelText(/display name/i)).toBeEnabled();
		expect(
			screen.getByRole("button", { name: /save changes/i }),
		).toBeEnabled();
	});

	it("offers Cancel in the dialog and Close when nothing is editable", async () => {
		const onCancel = vi.fn();
		const { user, unmount } = renderWithProviders(
			<UserProfileForm
				user={makeUser()}
				variant="dialog"
				onCancel={onCancel}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		expect(onCancel).toHaveBeenCalledOnce();
		unmount();

		authz.summary = summary(false, managed("users.read"));
		renderWithProviders(
			<UserProfileForm
				user={makeUser()}
				variant="dialog"
				onCancel={onCancel}
			/>,
		);
		expect(
			screen.getByRole("button", { name: "Close" }),
		).toBeInTheDocument();
	});

	it("has no Cancel on the page", () => {
		renderWithProviders(
			<UserProfileForm user={makeUser()} variant="page" />,
		);

		expect(
			screen.queryByRole("button", { name: "Cancel" }),
		).not.toBeInTheDocument();
	});

	it("reports a pending save as busy and preserves the draft after failure", async () => {
		let rejectSave!: (error: Error) => void;
		mockUpdateMutate.mockImplementationOnce(
			() =>
				new Promise((_resolve, reject) => {
					rejectSave = reject;
				}),
		);
		const onBusyChange = vi.fn();
		const { user } = renderWithProviders(
			<UserProfileForm
				user={makeUser()}
				variant="page"
				onBusyChange={onBusyChange}
			/>,
		);
		await user.clear(screen.getByLabelText(/display name/i));
		await user.type(
			screen.getByLabelText(/display name/i),
			"Preserved draft",
		);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		expect(onBusyChange).toHaveBeenLastCalledWith(true);
		expect(
			screen.getByLabelText(/display name/i).closest("[inert]"),
		).not.toBeNull();
		rejectSave(new Error("Synthetic user save failure"));
		const error = await screen.findByRole("alert");
		await waitFor(() => expect(error).toHaveFocus());
		expect(onBusyChange).toHaveBeenLastCalledWith(false);
		expect(screen.getByLabelText(/display name/i)).toHaveValue(
			"Preserved draft",
		);
		expect(
			screen.getByRole("button", { name: "Save Changes" }),
		).toBeEnabled();
	});
});
