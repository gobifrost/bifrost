import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { FormField } from "@/lib/client-types";
import { FormLayoutEditor } from "./FormLayoutEditor";

const fields: FormField[] = [
	{ name: "first", label: "First", type: "text" },
	{ name: "last", label: "Last", type: "text" },
];

describe("FormLayoutEditor", () => {
	it("keeps legacy forms vertical until the designer chooses to customize", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<FormLayoutEditor
				fields={fields}
				layout={null}
				onChange={onChange}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Customize Layout" }),
		);
		expect(onChange).toHaveBeenCalledWith({
			sections: [
				{
					rows: [
						{ columns: [{ field: "first", width: 12 }] },
						{ columns: [{ field: "last", width: 12 }] },
					],
				},
			],
		});
	});

	it("provides visible row controls and keyboard-accessible movement", () => {
		renderWithProviders(
			<FormLayoutEditor
				fields={fields}
				layout={{
					sections: [
						{
							title: "Contact",
							rows: [
								{ columns: [{ field: "first", width: 12 }] },
							],
						},
					],
				}}
				onChange={vi.fn()}
			/>,
		);

		expect(screen.getByText("Row 1")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Add Row" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Move row 1 up" }),
		).toBeDisabled();
		expect(screen.getByLabelText("Add field to row 1")).toBeInTheDocument();
	});
});
