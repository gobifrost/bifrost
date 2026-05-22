/**
 * Tests for RolesMultiSelect.
 *
 * Covers: placeholder summary, single-vs-many summary, hooks-mock-driven
 * filter, toggling a row fires onChange with the expected next array.
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent } from "@testing-library/react";

import { renderWithProviders, screen } from "@/test-utils";

const mockRoles = [
	{
		id: "r1",
		name: "Auditor",
		description: "Read-only auditor access",
		permissions: {},
		created_by: "x",
		created_at: "2026-05-21T00:00:00Z",
		updated_at: "2026-05-21T00:00:00Z",
	},
	{
		id: "r2",
		name: "Operator",
		description: "Day-to-day operator",
		permissions: {},
		created_by: "x",
		created_at: "2026-05-21T00:00:00Z",
		updated_at: "2026-05-21T00:00:00Z",
	},
];

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({ data: mockRoles, isLoading: false }),
}));

import { RolesMultiSelect } from "./RolesMultiSelect";

describe("RolesMultiSelect", () => {
	it("shows the placeholder when nothing is selected", () => {
		renderWithProviders(<RolesMultiSelect value={[]} onChange={() => {}} />);
		expect(screen.getByText(/select roles/i)).toBeInTheDocument();
	});

	it("shows the role name when exactly one role is selected", () => {
		renderWithProviders(
			<RolesMultiSelect value={["r1"]} onChange={() => {}} />,
		);
		// The button shows the single role's name as its summary.
		expect(screen.getByRole("combobox")).toHaveTextContent("Auditor");
	});

	it("shows a count summary when 2+ roles are selected", () => {
		renderWithProviders(
			<RolesMultiSelect value={["r1", "r2"]} onChange={() => {}} />,
		);
		expect(screen.getByRole("combobox")).toHaveTextContent("2 selected");
	});

	it("fires onChange with the new selection when an option is clicked", () => {
		const onChange = vi.fn();
		renderWithProviders(<RolesMultiSelect value={[]} onChange={onChange} />);

		// Open popover
		fireEvent.click(screen.getByRole("combobox"));

		// Click "Operator" — cmdk renders items by their `value` (role.name)
		fireEvent.click(screen.getByText("Operator"));

		expect(onChange).toHaveBeenCalledWith(["r2"]);
	});

	it("toggles an existing selection off when re-clicked", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<RolesMultiSelect value={["r1", "r2"]} onChange={onChange} />,
		);

		fireEvent.click(screen.getByRole("combobox"));
		fireEvent.click(screen.getByText("Auditor"));

		expect(onChange).toHaveBeenCalledWith(["r2"]);
	});
});
