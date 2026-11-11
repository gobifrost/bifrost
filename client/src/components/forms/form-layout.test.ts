import { describe, expect, it } from "vitest";
import type { FormField } from "@/lib/client-types";
import { createVerticalLayout, synchronizeFormLayout } from "./form-layout";

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
		const renamed = [
			{ ...fields[0]!, name: "given" },
			fields[1]!,
		];
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
});
