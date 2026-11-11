import { expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { FormCanvas } from "./FormCanvas";
vi.mock("@atlaskit/pragmatic-drag-and-drop/element/adapter", () => ({
	draggable: () => () => {},
	dropTargetForElements: () => () => {},
}));
vi.mock("@atlaskit/pragmatic-drag-and-drop/combine", () => ({
	combine: () => () => {},
}));
vi.mock("@atlaskit/pragmatic-drag-and-drop-auto-scroll/element", () => ({
	autoScrollForElements: () => () => {},
}));
it("offers an empty column as a directly addressable field-creation target", async () => {
	const request = vi.fn();
	const { user } = renderWithProviders(
		<FormCanvas
			fields={[]}
			layout={{
				sections: [
					{
						rows: [
							{
								columns: [
									{ field: null, width: 6 },
									{ field: null, width: 6 },
								],
							},
						],
					},
				],
			}}
			onChange={vi.fn()}
			onRequestField={request}
			onEdit={vi.fn()}
			onDelete={vi.fn()}
			onAnnounce={vi.fn()}
		/>,
	);
	await user.click(
		screen.getByRole("button", {
			name: "Add field to column 2 of row 1 in section 1",
		}),
	);
	expect(request).toHaveBeenCalledWith(
		{ type: "template" },
		{ section: 0, row: 0, column: 1 },
	);
});
it("changes a row preset without creating field records", async () => {
	const changed = vi.fn();
	const { user } = renderWithProviders(
		<FormCanvas
			fields={[]}
			layout={{
				sections: [
					{
						rows: [
							{
								columns: [
									{ field: null, width: 6 },
									{ field: null, width: 6 },
								],
							},
						],
					},
				],
			}}
			onChange={changed}
			onRequestField={vi.fn()}
			onEdit={vi.fn()}
			onDelete={vi.fn()}
			onAnnounce={vi.fn()}
		/>,
	);
	await user.selectOptions(
		screen.getByRole("combobox", {
			name: "Columns for row 1 in section 1",
		}),
		"4,4,4",
	);
	expect(changed.mock.lastCall![0].sections[0].rows[0].columns).toEqual([
		{ field: null, width: 4 },
		{ field: null, width: 4 },
		{ field: null, width: 4 },
	]);
});
