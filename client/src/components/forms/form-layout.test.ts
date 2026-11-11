import { describe, expect, it } from "vitest";
import {
	createVerticalLayout,
	createCanvasRow,
	placeCanvasField,
	moveCanvasField,
	resizeCanvasRow,
	moveCanvasRow,
	moveCanvasSection,
	synchronizeFormLayout,
} from "./form-layout";
const fields = [
	{ name: "first", label: "First", type: "text" as const },
	{ name: "last", label: "Last", type: "text" as const },
];
describe("form canvas state", () => {
	it("derives vertical rows without persisting layout on a metadata-only save", () => {
		expect(
			createVerticalLayout(fields).sections[0]!.rows.map(
				(r) => r.columns[0]!.field,
			),
		).toEqual(["first", "last"]);
		expect(synchronizeFormLayout(null, fields, fields)).toBeNull();
		expect(createVerticalLayout([]).sections).toEqual([]);
	});
	it("places new fields directly into a row slot without appending duplicate placements", () => {
		const layout = { sections: [{ rows: [createCanvasRow()] }] };
		const next = placeCanvasField(layout, "first", {
			section: 0,
			row: 0,
			column: 1,
		});
		expect(next.sections[0]!.rows[0]!.columns).toEqual([
			{ field: null, width: 6 },
			{ field: "first", width: 6 },
		]);
		expect(layout.sections[0]!.rows[0]!.columns[1]!.field).toBeNull();
	});
	it("keeps source slots and widths when moving or swapping existing fields", () => {
		const layout = {
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "first", width: 4 as const },
								{ field: "last", width: 8 as const },
							],
						},
						createCanvasRow(),
					],
				},
			],
		};
		const moved = moveCanvasField(layout, "first", {
			section: 0,
			row: 1,
			column: 0,
		});
		expect(moved.sections[0]!.rows[0]!.columns[0]).toEqual({
			field: null,
			width: 4,
		});
		expect(moved.sections[0]!.rows[1]!.columns[0]).toEqual({
			field: "first",
			width: 6,
		});
		const swapped = moveCanvasField(layout, "first", {
			section: 0,
			row: 0,
			column: 1,
		});
		expect(swapped.sections[0]!.rows[0]!.columns).toEqual([
			{ field: "last", width: 4 },
			{ field: "first", width: 8 },
		]);
	});
	it("never discards occupied fields when reducing the number of columns", () => {
		const row = {
			columns: [
				{ field: "first", width: 6 as const },
				{ field: "last", width: 6 as const },
			],
		};
		expect(resizeCanvasRow(row, "12")).toBeNull();
		expect(
			resizeCanvasRow(row, "4,4,4")?.columns.map((c) => c.field),
		).toEqual(["first", "last", null]);
	});
	it("preserves empty slots through field renames and deletes", () => {
		const layout = {
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "first", width: 6 as const },
								{ field: null, width: 6 as const },
							],
						},
					],
				},
			],
		};
		const renamed = synchronizeFormLayout(
			layout,
			[fields[0]!],
			[{ ...fields[0]!, name: "renamed" }],
		);
		expect(
			renamed?.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual(["renamed", null]);
		expect(
			synchronizeFormLayout(
				layout,
				[fields[0]!],
				[],
			)?.sections[0]!.rows[0]!.columns.map((c) => c.field),
		).toEqual([null, null]);
	});
	it("moves rows into empty sections and reorders whole sections", () => {
		const layout = {
			sections: [
				{ title: "First", rows: [createCanvasRow()] },
				{ title: "Second", rows: [] },
			],
		};
		const moved = moveCanvasRow(
			layout,
			{ section: 0, row: 0 },
			{ section: 1, row: 0 },
		);
		expect(moved.sections.map((s) => s.rows.length)).toEqual([0, 1]);
		expect(
			moveCanvasSection(layout, 1, 0).sections.map((s) => s.title),
		).toEqual(["Second", "First"]);
	});
});
