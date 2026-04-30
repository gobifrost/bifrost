import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TableAccessEditor } from "./TableAccessEditor";

describe("TableAccessEditor", () => {
	it("renders three scope cards with four checkboxes each", () => {
		render(<TableAccessEditor value={null} roles={[]} onChange={() => {}} />);
		expect(screen.getByText(/Everyone/)).toBeInTheDocument();
		expect(screen.getByText(/Role/)).toBeInTheDocument();
		expect(screen.getByText(/Creator/)).toBeInTheDocument();
		expect(screen.getAllByRole("checkbox").length).toBe(12);
	});

	it("emits onChange when a flag toggles", () => {
		const handler = vi.fn();
		render(<TableAccessEditor value={null} roles={[]} onChange={handler} />);
		const checkbox = screen.getByLabelText(/Everyone — Read/i);
		fireEvent.click(checkbox);
		expect(handler).toHaveBeenCalled();
		const arg = handler.mock.calls[0][0];
		expect(arg.everyone.read).toBe(true);
	});

	it("shows role multi-select with available roles", async () => {
		render(
			<TableAccessEditor
				value={null}
				roles={[{ id: "r1", name: "Role A" }]}
				onChange={() => {}}
			/>,
		);
		// Open the role combobox to see available options
		const trigger = screen.getByRole("combobox");
		fireEvent.click(trigger);
		await waitFor(() =>
			expect(screen.getByText("Role A")).toBeInTheDocument(),
		);
	});
});
