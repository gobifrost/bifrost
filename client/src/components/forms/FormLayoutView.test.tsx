import { expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { FormLayoutView } from "./FormLayoutView";
it("preserves empty column widths while hiding empty rows and section headings", () => {
	renderWithProviders(
		<FormLayoutView
			fields={[{ name: "name", type: "text", label: "Name" }]}
			layout={{
				sections: [
					{
						title: "Empty Section",
						rows: [{ columns: [{ field: null, width: 12 }] }],
					},
					{
						title: "Details",
						rows: [
							{
								columns: [
									{ field: null, width: 6 },
									{ field: "name", width: 6 },
								],
							},
						],
					},
				],
			}}
			renderField={(field) => <label>{field.label}</label>}
		/>,
	);
	expect(screen.queryByText("Empty Section")).not.toBeInTheDocument();
	expect(
		screen.getByRole("heading", { name: "Details" }),
	).toBeInTheDocument();
	const cell = screen.getByText("Name").parentElement!;
	expect(cell).toHaveClass("md:col-span-6");
	expect(cell.previousElementSibling).toHaveAttribute("aria-hidden", "true");
});

it("renders standalone root fields after named sections without a row wrapper", () => {
	const fields = [
		{ name: "first", label: "First", type: "text" as const },
		{ name: "last", label: "Last", type: "text" as const },
	];
	const { container } = renderWithProviders(
		<FormLayoutView
			fields={fields}
			layout={{
				sections: [
					{
						title: "Contact",
						rows: [{ columns: [{ field: "first", width: 12 }] }],
					},
					{
						title: null,
						description: null,
						rows: [{ field: "last", columns: [] }],
					},
				],
			}}
			renderField={(field) => <span>{field.label}</span>}
		/>,
	);
	expect(screen.getByText("Last")).toBeVisible();
	expect(screen.getByText("Last").parentElement!.className).toBe("");
	expect(container.querySelectorAll("h2")).toHaveLength(1);
});
