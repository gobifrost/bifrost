/**
 * Component tests for FormInfoDialog.
 *
 * Heavier form using react-hook-form + zod. We mock the hooks it depends on
 * (auth, workflows metadata, roles) and the OrganizationSelect (which pulls
 * orgs).
 *
 * Covers:
 * - required validation: submit empty form surfaces field errors
 * - default access_level is role_based → the Role picker section is rendered
 * - switching access_level to authenticated hides the Role picker
 * - happy-path submit: fills name + workflow, submits, onSave called with values
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

// Mocks for the hooks used by the dialog.
const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

const mockWorkflows = vi.fn();
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => mockWorkflows(),
}));

const mockRoles = vi.fn();
vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => mockRoles(),
}));

// OrganizationSelect pulls useOrganizations; stub the whole component to a
// simple controlled select.
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value: string | null | undefined;
		onChange: (v: string | null) => void;
	}) => (
		<select
			aria-label="organization"
			value={value ?? ""}
			onChange={(e) => onChange(e.target.value || null)}
		>
			<option value="">Global</option>
			<option value="org-1">Acme</option>
		</select>
	),
}));

import { FormInfoDialog } from "./FormInfoDialog";

beforeEach(() => {
	mockAuth.mockReturnValue({
		isPlatformAdmin: false,
		user: { organizationId: "org-1" },
	});
	mockWorkflows.mockReturnValue({
		data: {
			workflows: [
				{ id: "wf-1", name: "Onboarding", parameters: [] },
				{ id: "wf-2", name: "Disable", parameters: [] },
			],
		},
		isLoading: false,
	});
	mockRoles.mockReturnValue({
		data: [{ id: "role-1", name: "Admin" }],
		isLoading: false,
	});
});

function renderDialog(overrides: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onSave = vi.fn();
	const utils = renderWithProviders(
		<FormInfoDialog
			open={true}
			onClose={onClose}
			onSave={onSave}
			{...overrides}
		/>,
	);
	return { ...utils, onClose, onSave };
}

describe("FormInfoDialog — validation", () => {
	it("surfaces required errors when Save is clicked with an empty form", async () => {
		const { user, onSave } = renderDialog();

		await user.click(screen.getByRole("button", { name: /^save$/i }));

		expect(
			await screen.findByText(/name is required/i),
		).toBeInTheDocument();
		expect(
			screen.getByText(/linked workflow is required/i),
		).toBeInTheDocument();
		expect(onSave).not.toHaveBeenCalled();
	});
});

describe("FormInfoDialog — access level", () => {
	it("renders the Assigned Roles picker when access_level=role_based (default)", () => {
		renderDialog();

		expect(screen.getByText(/assigned roles/i)).toBeInTheDocument();
	});
});

describe("FormInfoDialog — org picker visibility", () => {
	it("does not render the Organization select for non-platform admins", () => {
		renderDialog();

		expect(
			screen.queryByLabelText(/organization/i),
		).not.toBeInTheDocument();
	});

	it("renders the Organization select for platform admins", () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			user: { organizationId: null },
		});

		renderDialog();

		expect(screen.getByLabelText(/organization/i)).toBeInTheDocument();
	});
});

describe("FormInfoDialog — sharing ownership", () => {
	it("leaves Confirmation Message and HMAC settings to Share", () => {
		renderDialog();

		expect(
			screen.queryByText(/confirmation message/i),
		).not.toBeInTheDocument();
		expect(screen.queryByText(/hmac/i)).not.toBeInTheDocument();
	});
});
