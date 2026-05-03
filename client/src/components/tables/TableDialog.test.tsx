/**
 * Component tests for TableDialog.
 *
 * Focus on behaviours:
 * - zod name regex: rejects uppercase / starts-with-digit names with an error
 * - create-mode submit: payload includes parsed schema JSON and scope query
 * - edit-mode: name is disabled + pre-filled, submit sends update with table_id
 * - invalid schema JSON blocks submit and surfaces an error
 * - OrganizationSelect only renders for platform admins
 * - PolicyEditor → mutate round-trip: a policy authored in the embedded
 *   PolicyEditor reaches the create/update mutation body verbatim, including
 *   the `when` JSON expression. This is the security-critical integration
 *   point: any drift between what the editor emits and what the API receives
 *   would let users save a policy that does not match what the UI told them.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import {
	renderWithProviders,
	screen,
	waitFor,
	fireEvent,
} from "@/test-utils";
import type { ReactNode } from "react";
import { POLICY_TEMPLATES } from "./policy-templates";

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

// PolicyEditor mounts a Monaco editor; replace it with a labelled <textarea>
// so the test can drive the JSON buffer via fireEvent.change.
vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
		path,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
		path?: string;
	}) => (
		<textarea
			aria-label={path ?? "monaco-editor"}
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

// Radix Select uses pointer events that jsdom doesn't fully implement; swap
// for a native <select>. Children (SelectItem) register their values into a
// shared context so the parent <select> shows them as <option>s.
vi.mock("@/components/ui/select", async () => {
	const React = await import("react");
	type Item = { value: string; label: string };
	const Ctx = React.createContext<{
		register: (it: Item) => void;
	} | null>(null);

	function Select({
		value,
		onValueChange,
		children,
	}: {
		value?: string;
		onValueChange?: (v: string) => void;
		children: ReactNode;
	}) {
		const [items, setItems] = React.useState<Item[]>([]);
		const register = React.useCallback((it: Item) => {
			setItems((prev) =>
				prev.some((p) => p.value === it.value) ? prev : [...prev, it],
			);
		}, []);
		return (
			<Ctx.Provider value={{ register }}>
				<select
					aria-label="Insert template"
					value={value ?? ""}
					onChange={(e) => onValueChange?.(e.target.value)}
				>
					<option value="">Insert template...</option>
					{items.map((it) => (
						<option key={it.value} value={it.value}>
							{it.label}
						</option>
					))}
				</select>
				<div style={{ display: "none" }}>{children}</div>
			</Ctx.Provider>
		);
	}
	const Pass = ({ children }: { children: ReactNode }) => <>{children}</>;
	function SelectItem({
		value,
		children,
	}: {
		value: string;
		children: ReactNode;
	}) {
		const ctx = React.useContext(Ctx);
		React.useEffect(() => {
			ctx?.register({ value, label: String(children) });
		}, [ctx, value, children]);
		return null;
	}
	return {
		Select,
		SelectContent: Pass,
		SelectGroup: Pass,
		SelectItem,
		SelectLabel: Pass,
		SelectScrollDownButton: () => null,
		SelectScrollUpButton: () => null,
		SelectSeparator: () => null,
		SelectTrigger: Pass,
		SelectValue: () => null,
	};
});

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
		// Schema field is a Monaco editor mocked as a textarea labelled by its `path` prop.
		fireEvent.change(screen.getByLabelText("table-schema.json"), {
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
		// Schema field is a Monaco editor mocked as a textarea labelled by its `path` prop.
		fireEvent.change(screen.getByLabelText("table-schema.json"), {
			target: { value: '{"type":"object"}' },
		});

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		const call = mockCreateMutate.mock.calls[0]![0];
		expect(call.body).toEqual({
			name: "tickets",
			description: "Support tickets",
			schema: { type: "object" },
			policies: null,
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
				policies: null,
			},
		});
	});
});

describe("TableDialog — PolicyEditor save round-trip (security)", () => {
	// Two complementary surfaces:
	//   1. Create-mode toolbar Insert Template flow (template → policies in
	//      submit body).
	//   2. Edit-mode JSON-tab edit (typed JSON → policies in update body).
	// Both paths must reach the create/update mutation body verbatim — any
	// drift between what the editor emits and what the API receives is a
	// silent policy bypass.
	it("creates a table whose policies body matches the inserted template", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<TableDialog open={true} onClose={onClose} />,
		);

		await user.type(screen.getByLabelText(/table name/i), "secrets");

		// Drive the toolbar Insert Template Select to add the `own_row`
		// template. The mock above swaps Radix Select for a native <select>
		// labelled `Insert template`; firing a change event with the template
		// key invokes onValueChange in the real component.
		const selectTrigger = screen.getByLabelText(
			/insert template/i,
		) as HTMLSelectElement;
		selectTrigger.value = "own_row";
		selectTrigger.dispatchEvent(new Event("change", { bubbles: true }));

		await user.click(screen.getByRole("button", { name: /^create$/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		const call = mockCreateMutate.mock.calls[0]![0];
		// The body's `policies` MUST mirror the template the user inserted,
		// including the template's `when` AST. Any drift between what the
		// editor emits and what the API receives = silent policy bypass.
		const expectedTemplate = POLICY_TEMPLATES.own_row!;
		expect(call.body.policies).toEqual({
			policies: [
				{
					name: "own_row",
					description: expectedTemplate.description,
					actions: expectedTemplate.actions,
					when: expectedTemplate.when,
				},
			],
		});
	});

	it("edit-mode: typing modified JSON in the JSON tab round-trips through update mutation", async () => {
		const table = {
			id: "tbl-policy",
			name: "existing_table",
			description: "",
			schema: null,
			organization_id: "org-1",
			policies: {
				policies: [
					{
						name: "everyone_read",
						actions: ["read"] as Array<
							"read" | "create" | "update" | "delete"
						>,
						when: null as unknown,
					},
				],
			},
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

		// Pre-existing policy is shown in the JSON Monaco editor (mocked to a
		// labelled <textarea>). Drive a modified JSON value through fireEvent
		// to simulate the user editing the policy.
		const jsonEditor = screen.getByLabelText(
			"policies.json",
		) as HTMLTextAreaElement;
		const modified = {
			policies: [
				{
					name: "owner_only",
					actions: ["read", "update"],
					when: { eq: [{ row: "created_by" }, { user: "user_id" }] },
				},
			],
		};
		fireEvent.change(jsonEditor, {
			target: { value: JSON.stringify(modified, null, 2) },
		});

		await user.click(screen.getByRole("button", { name: /^update$/i }));

		await waitFor(() => expect(mockUpdateMutate).toHaveBeenCalled());
		const call = (mockUpdateMutate as Mock).mock.calls[0]![0];
		// Update body MUST carry the renamed policy + the toggled action.
		// If the dialog ever dropped local PolicyEditor state on submit, the
		// user would think they tightened access but the table would not
		// reflect the change.
		const updatedPolicies = call.body.policies as {
			policies: Array<{
				name: string;
				actions: string[];
				when: unknown;
			}>;
		};
		expect(updatedPolicies.policies[0]!.name).toBe("owner_only");
		expect(updatedPolicies.policies[0]!.actions).toEqual([
			"read",
			"update",
		]);
		expect(updatedPolicies.policies[0]!.when).toEqual({
			eq: [{ row: "created_by" }, { user: "user_id" }],
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
