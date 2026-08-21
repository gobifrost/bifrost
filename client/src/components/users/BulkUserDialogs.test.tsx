import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockBulkMutate = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useBulkUserOperation: () => ({ mutateAsync: mockBulkMutate, isPending: false }),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({ data: [{ id: "builder", name: "Builder" }], isLoading: false }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [{ id: "org-1", name: "Acme" }], isLoading: false }),
	useOrganizationGroups: () => ({ data: [], isLoading: false }),
}));

vi.mock("@/hooks/useAdministrativeBoundary", () => ({
	useAdministrativeBoundary: () => "platform",
	authorizationHeaders: (boundary: string) => ({
		"X-Bifrost-Boundary": boundary,
	}),
}));

vi.mock("@/components/users/RoleAssignmentEditor", async (importOriginal) => {
	const actual = await importOriginal<typeof import("./RoleAssignmentEditor")>();
	return {
		...actual,
		RoleAssignmentEditor: ({ onChange }: { onChange: (value: unknown[]) => void }) => (
			<button
				type="button"
				onClick={() =>
					onChange([
						{
							role_id: "builder",
							boundaries: [
								{ boundary_kind: "organization", organization_id: "org-1" },
								{ boundary_kind: "managed_organizations" },
							],
						},
					])
				}
			>
				Choose bulk access
			</button>
		),
	};
});

import { BulkReplaceRolesDialog } from "./BulkUserDialogs";

beforeEach(() => {
	mockBulkMutate.mockReset();
	mockBulkMutate.mockResolvedValue({ succeeded: ["u-1", "u-2"], failed: [] });
});

describe("BulkReplaceRolesDialog", () => {
	it("sends one explicit boundary-aware assignment set for every selected user", async () => {
		const users = ["u-1", "u-2"].map((id) => ({
			id,
			email: `${id}@example.com`,
			name: id,
			is_active: true,
			is_superuser: false,
			is_verified: true,
			is_registered: true,
			is_system: false,
			is_external: false,
			mfa_enabled: false,
			invite_status: "accepted" as const,
			organization_id: "org-1",
			created_at: "2026-08-19T00:00:00Z",
			updated_at: "2026-08-19T00:00:00Z",
			last_login: null,
		}));
		const { user } = renderWithProviders(
			<BulkReplaceRolesDialog
				open={true}
				onOpenChange={vi.fn()}
				users={users}
				onPartialFailure={vi.fn()}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /choose bulk access/i }));
		await user.click(screen.getByRole("button", { name: /replace roles/i }));

		await waitFor(() => expect(mockBulkMutate).toHaveBeenCalledTimes(1));
		expect(mockBulkMutate.mock.calls[0]?.[0]).toEqual({
			headers: { "X-Bifrost-Boundary": "platform" },
			body: {
				user_ids: ["u-1", "u-2"],
				operation: "replace_roles",
				role_assignments: [
					{
						role_id: "builder",
						boundaries: [
							{ boundary_kind: "organization", organization_id: "org-1" },
							{ boundary_kind: "managed_organizations" },
						],
					},
				],
			},
		});
	});
});
