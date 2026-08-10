/**
 * Component tests for RoleDialog.
 *
 * Covers:
 * - required-name validation surfaces an error and blocks submit
 * - create-mode submit with trimmed values + permissions
 * - edit-mode pre-fills from the role prop and submits patch with role_id
 * - permission toggle is included in the submit payload
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();

vi.mock("@/hooks/useRoles", () => ({
	useCreateRole: () => ({ mutateAsync: mockCreateMutate, isPending: false }),
	useUpdateRole: () => ({ mutateAsync: mockUpdateMutate, isPending: false }),
}));

import { RoleDialog } from "./RoleDialog";

type Role = Parameters<typeof RoleDialog>[0]["role"];

function makeRole(overrides: Partial<NonNullable<Role>> = {}): NonNullable<Role> {
	return {
		id: "role-1",
		name: "Admin",
		description: "Admin role",
		permissions: { can_promote_agent: true },
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
		organization_id: null,
		...overrides,
	} as NonNullable<Role>;
}

beforeEach(() => {
	mockCreateMutate.mockReset();
	mockCreateMutate.mockResolvedValue({});
	mockUpdateMutate.mockReset();
	mockUpdateMutate.mockResolvedValue({});
});

describe("RoleDialog — validation", () => {
	it("surfaces 'Name is required' when submitted empty", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<RoleDialog open={true} onClose={onClose} />,
		);

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		expect(await screen.findByText(/name is required/i)).toBeInTheDocument();
		expect(mockCreateMutate).not.toHaveBeenCalled();
	});
});

describe("RoleDialog — create mode", () => {
	it("submits name, description, and permissions", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<RoleDialog open={true} onClose={onClose} />,
		);

		await user.type(screen.getByLabelText(/role name/i), "Viewer");
		await user.type(
			screen.getByLabelText(/description/i),
			"Read-only access",
		);
		// Toggle permission on.
		await user.click(screen.getByRole("switch"));

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => {
			expect(mockCreateMutate).toHaveBeenCalledTimes(1);
		});
		expect(mockCreateMutate.mock.calls[0]![0]).toEqual({
			body: {
				name: "Viewer",
				description: "Read-only access",
				permissions: { can_promote_agent: true },
			},
		});
		expect(onClose).toHaveBeenCalled();
	});

	it("passes null description when the textarea is blank", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<RoleDialog open={true} onClose={onClose} />,
		);

		await user.type(screen.getByLabelText(/role name/i), "Viewer");
		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		expect(mockCreateMutate.mock.calls[0]![0].body.description).toBeNull();
	});
});

describe("RoleDialog — edit mode", () => {
	it("pre-fills fields from the role and submits a patch", async () => {
		const onClose = vi.fn();
		const role = makeRole();
		const { user } = renderWithProviders(
			<RoleDialog role={role} open={true} onClose={onClose} />,
		);

		// Pre-filled values.
		expect(screen.getByLabelText(/role name/i)).toHaveValue("Admin");
		expect(screen.getByLabelText(/description/i)).toHaveValue("Admin role");
		expect(screen.getByRole("switch")).toBeChecked();

		await user.clear(screen.getByLabelText(/role name/i));
		await user.type(screen.getByLabelText(/role name/i), "Admin Edited");

		await user.click(screen.getByRole("button", { name: /update/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0]![0]).toEqual({
			params: { path: { role_id: "role-1" } },
			body: {
				name: "Admin Edited",
				description: "Admin role",
				permissions: { can_promote_agent: true },
			},
		});
	});
});
