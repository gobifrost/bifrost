/**
 * Component tests for EditUserDialog.
 *
 * Covers:
 * - pre-fills fields from the user prop
 * - "editing your own account" notice appears when currentUser.id matches
 * - "No changes to save" branch short-circuits on unchanged submit
 * - happy-path submit sends only the name delta
 * - promoting to platform admin surfaces the promote notice
 *
 * The Combobox is stubbed to a <select> for the same reason as
 * CreateUserDialog.test.tsx — driving Radix popovers in happy-dom is slow
 * and brittle.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUpdateMutate = vi.fn();
const mockAssignMutate = vi.fn();
const mockRemoveMutate = vi.fn();
const mockOrganizations = vi.fn();
const mockOrganizationGroups = vi.fn();
const mockRoles = vi.fn();
const mockUserRoles = vi.fn();
const mockAuth = vi.fn();
const mockAuthorizationBoundary = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useUpdateUser: () => ({
		mutateAsync: mockUpdateMutate,
		isPending: false,
	}),
	useUserRoles: () => mockUserRoles(),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => mockRoles(),
	useAssignUsersToRole: () => ({
		mutateAsync: mockAssignMutate,
		isPending: false,
	}),
	useRemoveUserFromRole: () => ({
		mutateAsync: mockRemoveMutate,
		isPending: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockOrganizations(),
	useOrganizationGroups: () => mockOrganizationGroups(),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));
vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => mockAuthorizationBoundary(),
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

function makeUser(overrides: Partial<NonNullable<User>> = {}): NonNullable<User> {
	return {
		id: "u-1",
		email: "alice@example.com",
		name: "Alice",
		is_active: true,
		is_superuser: false,
		organization_id: "org-1",
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		last_login: null,
		...overrides,
	} as NonNullable<User>;
}

beforeEach(() => {
	mockUpdateMutate.mockReset();
	mockUpdateMutate.mockResolvedValue({});
	mockAssignMutate.mockReset();
	mockAssignMutate.mockResolvedValue({});
	mockRemoveMutate.mockReset();
	mockRemoveMutate.mockResolvedValue({});
	mockOrganizations.mockReturnValue({
		data: [
			{
				id: "org-1",
				name: "Acme",
				domain: "acme.com",
				is_provider: false,
			},
			{
				id: "org-provider",
				name: "Provider",
				domain: null,
				is_provider: true,
			},
		],
		isLoading: false,
	});
	mockRoles.mockReturnValue({ data: [] });
	mockOrganizationGroups.mockReturnValue({ data: [], isLoading: false });
	mockUserRoles.mockReturnValue({ data: [], isLoading: false });
	mockAuth.mockReturnValue({
		user: { id: "other-user", email: "admin@example.com" },
		isPlatformAdmin: false,
		hasRole: () => false,
	});
	mockAuthorizationBoundary.mockReturnValue({
		selectedBoundary: "organization:org-1",
		selectedTarget: { boundary: "organization:org-1", capabilities: ["organizations.read"] },
		targets: [
			{
				boundary: "organization:org-1",
				capabilities: ["organizations.read"],
			},
		],
		hasSelectedCapability: () => true,
	});
});

describe("EditUserDialog", () => {
	it("pre-fills the display name from the user prop", () => {
		renderWithProviders(
			<EditUserDialog user={makeUser()} open={true} onOpenChange={vi.fn()} />,
		);

		expect(screen.getByLabelText(/display name/i)).toHaveValue("Alice");
		expect(screen.getByLabelText(/email address/i)).toBeDisabled();
	});

	it("shows 'editing your own account' notice when editing self", () => {
		const user = makeUser();
		mockAuth.mockReturnValue({
			user: { id: user.id, email: user.email },
			isPlatformAdmin: false,
			hasRole: () => false,
		});

		renderWithProviders(
			<EditUserDialog user={user} open={true} onOpenChange={vi.fn()} />,
		);

		expect(
			screen.getByText(/editing your own account/i),
		).toBeInTheDocument();
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
		expect(call.body.name).toBe("Alice Updated");
		// Fields that weren't changed should be null so the API leaves them alone.
		expect(call.body.is_active).toBeNull();
		expect(call.body.is_superuser).toBeNull();
		expect(call.body.organization_id).toBeNull();
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

		// "No changes to save" short-circuits without hitting the mutation.
		expect(mockUpdateMutate).not.toHaveBeenCalled();
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("assigns Platform Admin through the Role model at the Platform boundary", async () => {
		mockRoles.mockReturnValue({
			data: [{ id: "platform-admin", name: "Platform Admin" }],
			isLoading: false,
		});
		const { user } = renderWithProviders(
			<EditUserDialog
				user={makeUser()}
				open={true}
				onOpenChange={vi.fn()}
			/>,
		);

		await user.selectOptions(
			screen.getByLabelText("role-assignment-editor-add-role"),
			"platform-admin",
		);
		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockAssignMutate).toHaveBeenCalledTimes(1));
		expect(mockAssignMutate.mock.calls[0]?.[0]).toEqual({
			params: { path: { role_id: "platform-admin" } },
			body: {
				user_ids: ["u-1"],
				boundaries: [{ boundary_kind: "platform" }],
			},
		});
	});
});
