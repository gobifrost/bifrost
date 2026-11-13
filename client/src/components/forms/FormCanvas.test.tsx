import { expect, it, vi } from "vitest";
import { renderWithProviders, screen, act } from "@/test-utils";
import { FormCanvas } from "./FormCanvas";
type DropOptions = Parameters<
	typeof import("@atlaskit/pragmatic-drag-and-drop/element/adapter").dropTargetForElements
>[0];
const targets = vi.hoisted(() => [] as DropOptions[]);
vi.mock("@atlaskit/pragmatic-drag-and-drop/element/adapter", () => ({
	draggable: () => () => {},
	dropTargetForElements: (options: DropOptions) => {
		targets.push(options);
		return () => {
			const i = targets.indexOf(options);
			if (i >= 0) targets.splice(i, 1);
		};
	},
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

it("labels object types and shows only the innermost insertion line during a drag", () => {
	renderWithProviders(
		<FormCanvas
			fields={[{ name: "first", label: "First", type: "text" }]}
			layout={{
				sections: [
					{
						title: "Contact",
						rows: [
							{
								columns: [
									{ field: "first", width: 6 },
									{ field: null, width: 6 },
								],
							},
						],
					},
				],
			}}
			onChange={vi.fn()}
			onRequestField={vi.fn()}
			onEdit={vi.fn()}
			onDelete={vi.fn()}
			onAnnounce={vi.fn()}
		/>,
	);
	expect(screen.getByText("Section")).toBeVisible();
	expect(screen.getByText("Row")).toBeVisible();
	expect(screen.getByText("Text Input")).toBeVisible();
	expect(screen.queryByText("Row 1")).not.toBeInTheDocument();
	const elements = [
		screen.getByRole("region", { name: "Form Canvas" }),
		screen.getByRole("region", { name: "Section 1" }),
		screen.getByRole("region", { name: "Row 1 in section 1" }),
		screen.getByRole("group", { name: "Column 2 of row 1 in section 1" }),
	];
	const configs = elements.map((element) =>
		targets.find((t) => t.element === element)!,
	);
	act(() => {
		for (const config of configs)
			config.onDrag?.({
				self: { data: { after: false } },
				source: { data: { type: "canvas-field" } },
				location: {
					current: { dropTargets: [{ element: elements[3] }] },
				},
			} as never);
	});
	act(() => {
		const data = configs[3]!.getData?.({
			input: { clientX: 0, clientY: 0 },
		} as never);
		configs[3]!.onDrag?.({
			self: { data },
			source: { data: { type: "canvas-field" } },
			location: { current: { dropTargets: [{ element: elements[3] }] } },
		} as never);
	});
	expect(
		screen.getByRole("status", {
			name: "Drop at Column 2 of row 1 in section 1",
		}),
	).toHaveClass("border-2");
	expect(screen.getAllByRole("status")).toHaveLength(1);
	expect(
		screen.getByRole("status", {
			name: "Drop at Column 2 of row 1 in section 1",
		}),
	).toBeInTheDocument();
	act(() => {
		for (const config of configs) config.onDragLeave?.({} as never);
	});
	expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
