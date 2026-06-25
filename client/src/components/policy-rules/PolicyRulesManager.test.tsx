import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PolicyRulesManager } from "./PolicyRulesManager";

// ---------------------------------------------------------------------------
// Service mocks
// ---------------------------------------------------------------------------
vi.mock("@/services/policyRules", () => ({
	listPolicyRules: vi.fn(),
	createPolicyRule: vi.fn(),
	updatePolicyRule: vi.fn(),
	deletePolicyRule: vi.fn(),
	policyRuleUsages: vi.fn(),
}));

// Silence toast in tests
vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

import {
	listPolicyRules,
	createPolicyRule,
	updatePolicyRule,
	deletePolicyRule,
	policyRuleUsages,
} from "@/services/policyRules";

const mockList = listPolicyRules as ReturnType<typeof vi.fn>;
const mockCreate = createPolicyRule as ReturnType<typeof vi.fn>;
const mockUpdate = updatePolicyRule as ReturnType<typeof vi.fn>;
const mockDelete = deletePolicyRule as ReturnType<typeof vi.fn>;
const mockUsages = policyRuleUsages as ReturnType<typeof vi.fn>;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const RULE_CUSTOM = {
	id: "00000000-0000-0000-0000-000000000001",
	organization_id: null,
	name: "custom_rule",
	domain: "file" as const,
	description: "A custom rule",
	body: { actions: ["read"], when: null },
	is_builtin: false,
	created_at: "2024-01-01T00:00:00Z",
	updated_at: "2024-01-01T00:00:00Z",
};

const RULE_BUILTIN = {
	...RULE_CUSTOM,
	id: "00000000-0000-0000-0000-000000000002",
	name: "admin_bypass",
	description: "Platform admins bypass all file policies",
	is_builtin: true,
};

const EMPTY_USAGES = { file_policies: [], tables: [], total: 0 };

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("PolicyRulesManager", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockUsages.mockResolvedValue(EMPTY_USAGES);
	});

	it("renders a list of rules including built-in", async () => {
		mockList.mockResolvedValue([RULE_CUSTOM, RULE_BUILTIN]);
		render(<PolicyRulesManager domain="file" />);

		await waitFor(() => {
			expect(screen.getByText("custom_rule")).toBeInTheDocument();
			expect(screen.getByText("admin_bypass")).toBeInTheDocument();
		});
	});

	it("shows built-in badge and hides edit/delete for is_builtin rules", async () => {
		mockList.mockResolvedValue([RULE_BUILTIN]);
		render(<PolicyRulesManager domain="file" />);

		await waitFor(() => screen.getByTestId("builtin-badge"));

		// No edit/delete buttons for built-in rule
		expect(screen.queryByTestId("policy-rule-edit-btn")).toBeNull();
		expect(screen.queryByTestId("policy-rule-delete-btn")).toBeNull();
	});

	it("shows edit and delete buttons for non-builtin rules", async () => {
		mockList.mockResolvedValue([RULE_CUSTOM]);
		render(<PolicyRulesManager domain="file" />);

		await waitFor(() => screen.getByTestId("policy-rule-edit-btn"));
		expect(screen.getByTestId("policy-rule-delete-btn")).toBeInTheDocument();
	});

	it("create flow: opens dialog, submits, reloads list", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([]);
		mockCreate.mockResolvedValue({ ...RULE_CUSTOM, name: "new_rule" });

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rules-create-btn"));

		await user.click(screen.getByTestId("policy-rules-create-btn"));
		// Dialog opened — fill name
		await user.clear(screen.getByLabelText("Name"));
		await user.type(screen.getByLabelText("Name"), "new_rule");

		// Re-mock list to return new rule after creation
		mockList.mockResolvedValue([{ ...RULE_CUSTOM, name: "new_rule" }]);

		await user.click(screen.getByRole("button", { name: "Create" }));

		await waitFor(() => {
			expect(mockCreate).toHaveBeenCalledWith(
				expect.objectContaining({
					name: "new_rule",
					domain: "file",
					body: { actions: ["read"], when: null },
				}),
			);
		});
	});

	it("edit flow: opens dialog pre-filled and calls updatePolicyRule", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([RULE_CUSTOM]);
		mockUpdate.mockResolvedValue({ ...RULE_CUSTOM, description: "Updated" });

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rule-edit-btn"));

		await user.click(screen.getByTestId("policy-rule-edit-btn"));

		// Name field should be pre-filled and disabled
		const nameInput = screen.getByLabelText("Name") as HTMLInputElement;
		expect(nameInput.value).toBe("custom_rule");
		expect(nameInput.disabled).toBe(true);

		// Change description
		const descInput = screen.getByLabelText("Description");
		await user.clear(descInput);
		await user.type(descInput, "Updated");

		mockList.mockResolvedValue([{ ...RULE_CUSTOM, description: "Updated" }]);
		await user.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => {
			expect(mockUpdate).toHaveBeenCalledWith(
				"file",
				"custom_rule",
				expect.objectContaining({ description: "Updated" }),
			);
		});
	});

	it("edit with usages: shows inline info banner, does NOT open blast-radius delete dialog", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([RULE_CUSTOM]);
		mockUsages.mockResolvedValue({
			file_policies: [{ id: "fp-1", location: "workspace", path: "reports/", organization_id: null }],
			tables: [{ id: "tb-1", name: "my_table", organization_id: null }],
			total: 2,
		});

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rule-edit-btn"));

		await user.click(screen.getByTestId("policy-rule-edit-btn"));

		// (a) edit dialog is open
		await waitFor(() => screen.getByRole("dialog"));
		expect(screen.getByRole("dialog")).toBeInTheDocument();

		// (b) informational usages banner is visible inside the dialog with the count
		await waitFor(() => screen.getByTestId("edit-usages-banner"));
		const banner = screen.getByTestId("edit-usages-banner");
		expect(banner).toBeInTheDocument();
		expect(banner).toHaveTextContent("1 file polic");
		expect(banner).toHaveTextContent("1 table");
		expect(banner).toHaveTextContent("Saving changes will apply everywhere it");

		// (c) blast-radius / delete AlertDialog is NOT open
		expect(screen.queryByTestId("blast-radius-dialog")).toBeNull();
		expect(screen.queryByText("You must remove all references first")).toBeNull();
	});

	it("delete: shows confirmation dialog, deletes on confirm", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([RULE_CUSTOM]);
		mockDelete.mockResolvedValue(undefined);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rule-delete-btn"));

		await user.click(screen.getByTestId("policy-rule-delete-btn"));
		// Confirmation dialog
		expect(screen.getByRole("alertdialog")).toBeInTheDocument();

		mockList.mockResolvedValue([]);
		await user.click(screen.getByRole("button", { name: "Delete" }));

		await waitFor(() => {
			expect(mockDelete).toHaveBeenCalledWith("file", "custom_rule");
		});
	});

	it("delete 409: shows blast-radius dialog with file policies and tables", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([RULE_CUSTOM]);

		const inUseError = new Error("Rule is in use") as Error & { cause: { type: "in_use"; message: string; usages: { file_policies: Array<{ id: string; location: string; path: string; organization_id: null }>; tables: Array<{ id: string; name: string; organization_id: null }>; total: number } } };
		inUseError.cause = {
			type: "in_use",
			message: "Policy rule 'custom_rule' is in use",
			usages: {
				file_policies: [{ id: "fp-1", location: "workspace", path: "reports/", organization_id: null }],
				tables: [{ id: "tb-1", name: "my_table", organization_id: null }],
				total: 2,
			},
		};
		mockDelete.mockRejectedValue(inUseError);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rule-delete-btn"));

		await user.click(screen.getByTestId("policy-rule-delete-btn"));
		await user.click(screen.getByRole("button", { name: "Delete" }));

		await waitFor(() => {
			expect(screen.getByTestId("blast-radius-dialog")).toBeInTheDocument();
		});

		// Both file policy and table should appear in the dialog
		expect(screen.getByTestId("blast-file-policy")).toBeInTheDocument();
		expect(screen.getByTestId("blast-table")).toBeInTheDocument();
	});

	it("shows empty state when no rules", async () => {
		mockList.mockResolvedValue([]);
		render(<PolicyRulesManager domain="table" />);

		await waitFor(() => {
			expect(screen.getByText(/No table policy rules yet/)).toBeInTheDocument();
		});
	});

	it("shows form error when body JSON is invalid", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([]);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rules-create-btn"));

		await user.click(screen.getByTestId("policy-rules-create-btn"));
		await user.type(screen.getByLabelText("Name"), "test_rule");

		const textarea = screen.getByTestId("rule-body-textarea");
		await user.clear(textarea);
		await user.type(textarea, "not valid json");

		await user.click(screen.getByRole("button", { name: "Create" }));

		await waitFor(() => {
			expect(screen.getByTestId("form-error")).toBeInTheDocument();
		});
		expect(mockCreate).not.toHaveBeenCalled();
	});

	it("disable create button when name is empty", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([]);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rules-create-btn"));

		await user.click(screen.getByTestId("policy-rules-create-btn"));

		// Name is empty by default — Create button should be disabled
		expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
	});

	it("cancel closes the dialog without saving", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([]);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rules-create-btn"));

		await user.click(screen.getByTestId("policy-rules-create-btn"));
		// Dialog is open
		expect(screen.getByRole("dialog")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Cancel" }));
		await waitFor(() => {
			expect(screen.queryByRole("dialog")).toBeNull();
		});
		expect(mockCreate).not.toHaveBeenCalled();
	});

	// Simulate a failed list load
	it("handles list load error gracefully", async () => {
		mockList.mockRejectedValue(new Error("Network error"));
		// Should not throw
		render(<PolicyRulesManager domain="file" />);
		// After the failed fetch the loading state resolves without crashing
		await waitFor(() => {
			expect(screen.getByTestId("policy-rules-manager")).toBeInTheDocument();
		});
	});

	it("does not fire delete when confirmation cancelled", async () => {
		const user = userEvent.setup();
		mockList.mockResolvedValue([RULE_CUSTOM]);

		render(<PolicyRulesManager domain="file" />);
		await waitFor(() => screen.getByTestId("policy-rule-delete-btn"));

		await user.click(screen.getByTestId("policy-rule-delete-btn"));
		// Click Cancel inside the alert dialog
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		expect(mockDelete).not.toHaveBeenCalled();
	});
});
