/**
 * Component tests for RoleDialog.
 *
 * Covers:
 * - required-name validation surfaces an error and blocks submit
 * - create-mode submit with selected capabilities
 * - edit-mode pre-fills from the role prop and submits patch with role_id
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { within } from "@testing-library/react";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();

vi.mock("@/hooks/useRoles", () => ({
	PLATFORM_BOUNDARY_HEADERS: { "X-Bifrost-Boundary": "platform" },
	useCreateRole: () => ({ mutateAsync: mockCreateMutate, isPending: false }),
	useUpdateRole: () => ({ mutateAsync: mockUpdateMutate, isPending: false }),
	useAuthorizationCapabilities: () => ({
		data: [
			{
				key: "organizations.readwrite",
				display_name: "Manage organizations",
				description: "Create and update organizations.",
				category: "Organizations",
				is_privileged: true,
				assignable_to_custom_roles: true,
			},
			{
				key: "roles.readwrite",
				display_name: "Manage roles",
				description: "Create and update roles.",
				category: "Roles",
				is_privileged: true,
				assignable_to_custom_roles: true,
			},
			{
				key: "builder.execute",
				display_name: "Use Builder",
				description: "Create and modify Builder projects.",
				category: "Solutions",
				is_privileged: true,
				assignable_to_custom_roles: true,
			},
		],
		isLoading: false,
	}),
}));

import { RoleDialog } from "./RoleDialog";

type Role = Parameters<typeof RoleDialog>[0]["role"];

function makeRole(overrides: Partial<NonNullable<Role>> = {}): NonNullable<Role> {
	return {
		id: "role-1",
		name: "Admin",
		description: "Admin role",
		capabilities: ["builder.execute"],
		is_builtin: false,
		assignable_to_resources: true,
		created_by: "test@example.com",
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
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
	it("submits name, description, and capabilities", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<RoleDialog open={true} onClose={onClose} />,
		);

		await user.type(screen.getByLabelText(/role name/i), "Viewer");
		await user.type(
			screen.getByLabelText(/description/i),
			"Read-only access",
		);
		await user.click(screen.getByRole("checkbox", { name: "Use Builder" }));

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => {
			expect(mockCreateMutate).toHaveBeenCalledTimes(1);
		});
		expect(mockCreateMutate.mock.calls[0]![0]).toEqual({
			headers: { "X-Bifrost-Boundary": "platform" },
			body: {
				name: "Viewer",
				description: "Read-only access",
				capabilities: ["builder.execute"],
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

	it("shows the privileged badge for privileged capabilities", () => {
		renderWithProviders(<RoleDialog open={true} onClose={vi.fn()} />);

		const organizationRow = screen
			.getByText("Manage organizations")
			.closest("label");
		const roleRow = screen.getByText("Manage roles").closest("label");

		expect(organizationRow).not.toBeNull();
		expect(roleRow).not.toBeNull();
		expect(
			within(organizationRow as HTMLElement).getByText("Privileged"),
		).toBeInTheDocument();
		expect(
			within(roleRow as HTMLElement).getByText("Privileged"),
		).toBeInTheDocument();
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
		expect(screen.getByRole("checkbox", { name: "Use Builder" })).toBeChecked();

		await user.clear(screen.getByLabelText(/role name/i));
		await user.type(screen.getByLabelText(/role name/i), "Admin Edited");

		await user.click(screen.getByRole("button", { name: /update/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0]![0]).toEqual({
			headers: { "X-Bifrost-Boundary": "platform" },
			params: { path: { role_id: "role-1" } },
			body: {
				name: "Admin Edited",
				description: "Admin role",
				capabilities: ["builder.execute"],
			},
		});
	});
});
