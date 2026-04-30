/**
 * Component tests for TableDialog.
 *
 * Focus on behaviours:
 * - zod name regex: rejects uppercase / starts-with-digit names with an error
 * - create-mode submit: payload includes parsed schema JSON and scope query
 * - edit-mode: name is disabled + pre-filled, submit sends update with table_id
 * - invalid schema JSON blocks submit and surfaces an error
 * - OrganizationSelect only renders for platform admins
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockAuth = vi.fn();

vi.mock("@/services/tables", () => ({
	useCreateTable: () => ({ mutateAsync: mockCreateMutate, isPending: false }),
	useUpdateTable: () => ({ mutateAsync: mockUpdateMutate, isPending: false }),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({ data: [] }),
}));

// OrganizationSelect pulls useOrganizations — stub to a simple select.
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value: string | null | undefined;
		onChange: (v: string | null) => void;
	}) => (
		<select
			aria-label="organization-select"
			value={value ?? ""}
			onChange={(e) => onChange(e.target.value || null)}
		>
			<option value="">Global</option>
			<option value="org-1">Acme</option>
		</select>
	),
}));

import { TableDialog } from "./TableDialog";

beforeEach(() => {
	mockCreateMutate.mockReset();
	mockCreateMutate.mockResolvedValue({});
	mockUpdateMutate.mockReset();
	mockUpdateMutate.mockResolvedValue({});
	mockAuth.mockReturnValue({
		isPlatformAdmin: false,
		user: { organizationId: "org-1" },
	});
});

describe("TableDialog — validation", () => {
	it("rejects a name with uppercase letters via the regex", async () => {
		const { user } = renderWithProviders(
			<TableDialog open={true} onClose={vi.fn()} />,
		);

		await user.type(screen.getByLabelText(/table name/i), "BadName");
		await user.click(screen.getByRole("button", { name: /^create$/i }));

		expect(
			await screen.findByText(/must start with a lowercase letter/i),
		).toBeInTheDocument();
		expect(mockCreateMutate).not.toHaveBeenCalled();
	});

	it("rejects an empty name with 'Name is required'", async () => {
		const { user } = renderWithProviders(
			<TableDialog open={true} onClose={vi.fn()} />,
		);
		await user.click(screen.getByRole("button", { name: /^create$/i }));
		expect(
			await screen.findByText(/name is required/i),
		).toBeInTheDocument();
	});

	it("surfaces 'Invalid JSON' when the schema field is malformed", async () => {
		const { user } = renderWithProviders(
			<TableDialog open={true} onClose={vi.fn()} />,
		);

		await user.type(screen.getByLabelText(/table name/i), "my_table");
		// fireEvent.change avoids userEvent.type interpreting `{` as a keyboard modifier.
		fireEvent.change(screen.getByLabelText(/^schema/i), {
			target: { value: "{not json" },
		});
		await user.click(screen.getByRole("button", { name: /^create$/i }));

		expect(
			await screen.findByText(/invalid json/i),
		).toBeInTheDocument();
		expect(mockCreateMutate).not.toHaveBeenCalled();
	});
});

describe("TableDialog — create mode", () => {
	it("submits with parsed JSON schema and scope query", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<TableDialog open={true} onClose={onClose} />,
		);

		await user.type(screen.getByLabelText(/table name/i), "tickets");
		await user.type(
			screen.getByLabelText(/description/i),
			"Support tickets",
		);
		// fireEvent.change avoids userEvent.type interpreting `{` as a keyboard modifier.
		fireEvent.change(screen.getByLabelText(/^schema/i), {
			target: { value: '{"type":"object"}' },
		});

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		const call = mockCreateMutate.mock.calls[0]![0];
		expect(call.body).toEqual({
			name: "tickets",
			description: "Support tickets",
			schema: { type: "object" },
			access: null,
		});
		// Non-admin default org is "org-1" → scope should be set.
		expect(call.params.query).toEqual({ scope: "org-1" });
		expect(onClose).toHaveBeenCalled();
	});
});

describe("TableDialog — edit mode", () => {
	it("pre-fills the form, disables the name input, and sends an update", async () => {
		const table = {
			id: "tbl-1",
			name: "existing_table",
			description: "Old desc",
			schema: { type: "object" },
			organization_id: "org-1",
			created_at: "2026-04-20T00:00:00Z",
			updated_at: "2026-04-20T00:00:00Z",
		};

		const { user } = renderWithProviders(
			<TableDialog
				table={
					table as unknown as Parameters<
						typeof TableDialog
					>[0]["table"]
				}
				open={true}
				onClose={vi.fn()}
			/>,
		);

		const nameInput = screen.getByLabelText(/table name/i);
		expect(nameInput).toHaveValue("existing_table");
		expect(nameInput).toBeDisabled();

		const desc = screen.getByLabelText(/description/i);
		await user.clear(desc);
		await user.type(desc, "Updated");

		await user.click(screen.getByRole("button", { name: /^update$/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		expect(mockUpdateMutate.mock.calls[0]![0]).toEqual({
			params: { path: { table_id: "tbl-1" } },
			body: {
				description: "Updated",
				schema: { type: "object" },
				access: null,
			},
		});
	});
});

describe("TableDialog — organization picker visibility", () => {
	it("does not render OrganizationSelect for non-platform admins", () => {
		renderWithProviders(<TableDialog open={true} onClose={vi.fn()} />);
		expect(
			screen.queryByLabelText(/organization-select/i),
		).not.toBeInTheDocument();
	});

	it("renders OrganizationSelect for platform admins", () => {
		mockAuth.mockReturnValue({
			isPlatformAdmin: true,
			user: { organizationId: null },
		});
		renderWithProviders(<TableDialog open={true} onClose={vi.fn()} />);
		expect(
			screen.getByLabelText(/organization-select/i),
		).toBeInTheDocument();
	});
});
