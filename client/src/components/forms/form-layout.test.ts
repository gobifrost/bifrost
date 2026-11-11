import { describe, expect, it } from "vitest";
import type { FormField } from "@/lib/client-types";
import {
	appendLayoutColumn,
	appendLayoutRow,
	createVerticalLayout,
	moveLayoutColumn,
	moveLayoutRow,
	moveLayoutSection,
	synchronizeFormLayout,
} from "./form-layout";

const fields: FormField[] = [
	{ name: "first", label: "First", type: "text" },
	{ name: "last", label: "Last", type: "text" },
];

describe("form layout helpers", () => {
	it("creates a vertical layout only when the designer opts in", () => {
		expect(createVerticalLayout(fields)).toEqual({
			sections: [
				{
					rows: [
						{ columns: [{ field: "first", width: 12 }] },
						{ columns: [{ field: "last", width: 12 }] },
					],
				},
			],
		});
		expect(synchronizeFormLayout(null, fields, fields)).toBeNull();
	});

	it("keeps an opted-in layout complete across a rename followed by an addition", () => {
		const layout = {
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "first", width: 6 as const },
								{ field: "last", width: 6 as const },
							],
						},
					],
				},
			],
		};
		const renamed = [{ ...fields[0]!, name: "given" }, fields[1]!];
		const renamedLayout = synchronizeFormLayout(layout, fields, renamed);
		expect(renamedLayout).toEqual({
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "given", width: 6 },
								{ field: "last", width: 6 },
							],
						},
					],
				},
			],
		});

		const next = [
			...renamed,
			{ name: "email", label: "Email", type: "email" as const },
		];
		expect(synchronizeFormLayout(renamedLayout, renamed, next)).toEqual({
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "given", width: 6 },
								{ field: "last", width: 6 },
							],
						},
						{ columns: [{ field: "email", width: 12 }] },
					],
				},
			],
		});
	});

	it("preserves existing placements when deleting the first field or reordering", () => {
		const layout = {
			sections: [
				{
					rows: [
						{ columns: [{ field: "first", width: 3 as const }] },
						{ columns: [{ field: "last", width: 9 as const }] },
					],
				},
			],
		};
		expect(synchronizeFormLayout(layout, fields, [fields[1]!])).toEqual({
			sections: [
				{
					rows: [
						{ columns: [] },
						{ columns: [{ field: "last", width: 9 }] },
					],
				},
			],
		});
		expect(
			synchronizeFormLayout(layout, fields, [fields[1]!, fields[0]!]),
		).toEqual(layout);
	});

	it("moves sections and rows across sections without duplicating content", () => {
		const layout = {
			sections: [
				{
					title: "First",
					rows: [
						{ columns: [{ field: "first", width: 12 as const }] },
					],
				},
				{
					title: "Second",
					rows: [
						{ columns: [{ field: "last", width: 12 as const }] },
					],
				},
			],
		};
		expect(
			moveLayoutSection(layout, 1, 0, "before").sections.map(
				(section) => section.title,
			),
		).toEqual(["Second", "First"]);
		expect(
			moveLayoutRow(
				layout,
				{ section: 0, row: 0 },
				{ section: 1, row: 0 },
				"after",
			).sections[1]!.rows.map((row) => row.columns[0]?.field),
		).toEqual(["last", "first"]);
		expect(
			appendLayoutRow(layout, { section: 0, row: 0 }, 1)?.sections[1]!
				.rows,
		).toHaveLength(2);
	});

	it("preserves a field width and rejects overflowing layout drops", () => {
		const layout = {
			sections: [
				{
					rows: [
						{
							columns: [
								{ field: "first", width: 6 as const },
								{ field: "last", width: 6 as const },
							],
						},
						{ columns: [] },
					],
				},
			],
		};
		const moved = appendLayoutColumn(
			layout,
			{ section: 0, row: 0, field: "first" },
			{ section: 0, row: 1 },
		);
		expect(moved.layout?.sections[0]!.rows[1]!.columns).toEqual([
			{ field: "first", width: 6 },
		]);
		const overflow = moveLayoutColumn(
			layout,
			{ section: 0, row: 0, field: "first" },
			{ section: 0, row: 0, field: "last" },
			"after",
		);
		expect(
			overflow.layout?.sections[0]!.rows[0]!.columns.map(
				(column) => column.field,
			),
		).toEqual(["last", "first"]);
		const blocked = appendLayoutColumn(
			{
				sections: [
					{
						rows: [
							{
								columns: [
									{ field: "first", width: 6 as const },
								],
							},
							{ columns: [{ field: "last", width: 9 as const }] },
						],
					},
				],
			},
			{ section: 0, row: 0, field: "first" },
			{ section: 0, row: 1 },
		);
		expect(blocked).toEqual({
			layout: null,
			error: "That row does not have enough width for this field.",
		});
	});
});
