import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUserRoles = vi.fn();
const mockRoles = vi.fn();
const mockOrganizations = vi.fn();
const mockOrganizationGroups = vi.fn();
const mockAssignMutate = vi.fn();
const mockRemoveMutate = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useUserRoles: () => mockUserRoles(),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => mockRoles(),
	useAssignUsersToRole: () => ({ mutateAsync: mockAssignMutate, isPending: false }),
	useRemoveUserFromRole: () => ({ mutateAsync: mockRemoveMutate, isPending: false }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockOrganizations(),
	useOrganizationGroups: () => mockOrganizationGroups(),
}));

vi.mock("@/hooks/useAdministrativeBoundary", () => ({
	useAdministrativeBoundary: () => "platform",
	organizationBoundary: (organizationId: string | null | undefined) =>
		organizationId ? `organization:${organizationId}` : "platform",
}));

vi.mock("@/components/users/RoleAssignmentEditor", async (importOriginal) => {
	const actual = await importOriginal<typeof import("./RoleAssignmentEditor")>();
	return {
		...actual,
		RoleAssignmentEditor: ({ value, onChange }: {
			value: Array<{ role_id: string; boundaries: Array<{ boundary_kind: string }> }>;
			onChange: (value: unknown[]) => void;
		}) => (
			<div>
				<span>Assignments: {value.length}</span>
				<button
					type="button"
					onClick={() =>
						onChange([
							{
								role_id: "r-2",
								boundaries: [{ boundary_kind: "managed_organizations" }],
							},
						])
					}
				>
					Choose Viewer
				</button>
			</div>
		),
	};
});

import { UserRolesDialog } from "./UserRolesDialog";

type User = Parameters<typeof UserRolesDialog>[0]["user"];

function makeUser(): NonNullable<User> {
	return {
		id: "u-1",
		email: "alice@example.com",
		name: "Alice",
		is_active: true,
		is_superuser: false,
		is_external: false,
		organization_id: "org-1",
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		last_login: null,
	} as NonNullable<User>;
}

beforeEach(() => {
	mockAssignMutate.mockReset();
	mockAssignMutate.mockResolvedValue({});
	mockRemoveMutate.mockReset();
	mockRemoveMutate.mockResolvedValue({});
	mockOrganizations.mockReturnValue({ data: [{ id: "org-1", name: "Acme" }], isLoading: false });
	mockOrganizationGroups.mockReturnValue({ data: [], isLoading: false });
	mockRoles.mockReturnValue({
		data: [
			{ id: "r-1", name: "Builder" },
			{ id: "r-2", name: "Viewer" },
		],
		isLoading: false,
	});
});

describe("UserRolesDialog", () => {
	it("loads durable assignments and waits for an explicit save", async () => {
		mockUserRoles.mockReturnValue({
			data: [
				{
					id: "a-1",
					user_id: "u-1",
					role_id: "r-1",
					assigned_at: "2026-08-19T00:00:00Z",
					boundaries: [
						{ id: "b-1", boundary_kind: "organization", organization_id: "org-1" },
					],
				},
			],
			isLoading: false,
		});

		renderWithProviders(
			<UserRolesDialog user={makeUser()} open={true} onClose={vi.fn()} />,
		);

		expect(await screen.findByText("Assignments: 1")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /save access/i })).toBeDisabled();
	});

	it("replaces changed assignments and removes roles omitted from the draft", async () => {
		mockUserRoles.mockReturnValue({
			data: [
				{
					id: "a-1",
					user_id: "u-1",
					role_id: "r-1",
					assigned_at: "2026-08-19T00:00:00Z",
					boundaries: [{ id: "b-1", boundary_kind: "organization", organization_id: "org-1" }],
				},
			],
			isLoading: false,
		});
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<UserRolesDialog user={makeUser()} open={true} onClose={onClose} />,
		);

		await user.click(await screen.findByRole("button", { name: /choose viewer/i }));
		await user.click(screen.getByRole("button", { name: /save access/i }));

		await waitFor(() => expect(mockAssignMutate).toHaveBeenCalledTimes(1));
		expect(mockAssignMutate.mock.calls[0]?.[0]).toEqual({
			params: { path: { role_id: "r-2" } },
			body: {
				user_ids: ["u-1"],
				boundaries: [{ boundary_kind: "managed_organizations" }],
			},
		});
		expect(mockRemoveMutate).toHaveBeenCalledWith({
			params: { path: { role_id: "r-1", user_id: "u-1" } },
		});
		expect(onClose).toHaveBeenCalled();
	});

	it("shows a loading state while assignments are being resolved", () => {
		mockUserRoles.mockReturnValue({ data: undefined, isLoading: true });
		renderWithProviders(
			<UserRolesDialog user={makeUser()} open={true} onClose={vi.fn()} />,
		);
		expect(screen.queryByText(/assignments:/i)).not.toBeInTheDocument();
	});
});
