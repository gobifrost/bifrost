/**
 * Component tests for EditUserDialog.
 *
 * Covers the dialog around UserProfileForm (which has its own tests):
 * - title and protected-account notice follow what the caller may change
 * - a save closes it; a pending save keeps it open
 * - role assignments live on the person page, not here
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
	it("shows the profile form with a close control", () => {
		renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: "Edit User" }),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/display name/i)).toHaveValue("Alice");
		expect(
			screen.getByRole("button", { name: /close dialog/i }),
		).toBeInTheDocument();
	});

	it("closes after a save", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={onOpenChange}
			/>,
		);

		await user.clear(screen.getByLabelText(/display name/i));
		await user.type(screen.getByLabelText(/display name/i), "Alice B");
		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
		expect(mockUpdateMutate).toHaveBeenCalledOnce();
	});

	it("shows a protected user's details read-only to anyone but a Platform Admin", () => {
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
		expect(
			screen.queryByRole("button", { name: /save changes/i }),
		).not.toBeInTheDocument();
	});

	it("tells a Platform Admin a protected user is theirs alone to change", () => {
		renderWithProviders(
			<EditUserDialog
				user={makeUser({ is_protected: true })}
				open
				onOpenChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByText(
				"This person holds privileged access. Only Platform Admins can change their profile, sign-in, or roles.",
			),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /save changes/i }),
		).toBeEnabled();
	});

	it("edits the profile only; role assignments live on the person page", () => {
		renderWithProviders(
			<EditUserDialog user={makeUser()} open onOpenChange={vi.fn()} />,
		);

		expect(screen.queryByRole("tab")).not.toBeInTheDocument();
		expect(screen.queryByText("Roles & access")).not.toBeInTheDocument();
	});
});

it("keeps a pending save open", async () => {
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
	rejectSave(new Error("Synthetic user save failure"));
	await waitFor(() =>
		expect(
			screen.getByRole("button", { name: "Close dialog" }),
		).toBeEnabled(),
	);
});
