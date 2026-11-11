import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";

type GeneratedLayout = components["schemas"]["FormLayout"];
type GeneratedSection = NonNullable<GeneratedLayout["sections"]>[number];
type GeneratedRow = NonNullable<GeneratedSection["rows"]>[number];
type GeneratedColumn = NonNullable<GeneratedRow["columns"]>[number];
export const FORM_LAYOUT_WIDTHS = [3, 4, 6, 8, 9, 12] as const;
export type FormLayoutColumn = Omit<GeneratedColumn, "width"> & {
	width: (typeof FORM_LAYOUT_WIDTHS)[number];
};
export type FormLayoutRow = Omit<GeneratedRow, "columns"> & {
	columns: FormLayoutColumn[];
};
export type FormLayoutSection = Omit<GeneratedSection, "rows"> & {
	rows: FormLayoutRow[];
};
export type FormLayout = Omit<GeneratedLayout, "sections"> & {
	sections: FormLayoutSection[];
};
export interface FormCanvasState {
	fields: FormField[];
	layout: FormLayout | null;
}
export interface CanvasTarget {
	section: number;
	row: number;
	column?: number;
}
export const formLayoutWidthClass: Record<number, string> = {
	3: "col-span-12 md:col-span-3",
	4: "col-span-12 md:col-span-4",
	6: "col-span-12 md:col-span-6",
	8: "col-span-12 md:col-span-8",
	9: "col-span-12 md:col-span-9",
	12: "col-span-12",
};
export const ROW_PRESETS = [
	{ value: "12", label: "1 Column", widths: [12] },
	{ value: "6,6", label: "2 Columns", widths: [6, 6] },
	{ value: "4,4,4", label: "3 Columns", widths: [4, 4, 4] },
	{ value: "3,3,3,3", label: "4 Columns", widths: [3, 3, 3, 3] },
	{ value: "4,8", label: "2 Columns (1/3 + 2/3)", widths: [4, 8] },
	{ value: "8,4", label: "2 Columns (2/3 + 1/3)", widths: [8, 4] },
	{ value: "3,9", label: "2 Columns (1/4 + 3/4)", widths: [3, 9] },
	{ value: "9,3", label: "2 Columns (3/4 + 1/4)", widths: [9, 3] },
] as const;
export function normalizeFormLayout(
	layout: GeneratedLayout | null | undefined,
): FormLayout | null {
	if (!layout) return null;
	return {
		...layout,
		sections: (layout.sections ?? []).map((s) => ({
			...s,
			rows: (s.rows ?? []).map((r) => ({
				...r,
				columns: (r.columns ?? []).map((c) => ({
					...c,
					field: c.field ?? null,
				})),
			})),
		})),
	};
}
export function createVerticalLayout(fields: FormField[]): FormLayout {
	return {
		sections: fields.length
			? [
					{
						rows: fields.map((f) => ({
							columns: [{ field: f.name, width: 12 }],
						})),
					},
				]
			: [],
	};
}
export function createCanvasRow(preset = "6,6"): FormLayoutRow {
	const item = ROW_PRESETS.find((p) => p.value === preset)!;
	return { columns: item.widths.map((width) => ({ field: null, width })) };
}
export function placeCanvasField(
	layout: FormLayout,
	field: string,
	target: CanvasTarget,
): FormLayout {
	const next = structuredClone(layout);
	if (!next.sections[target.section]) next.sections.push({ rows: [] });
	const section = next.sections[target.section]!;
	if (target.column !== undefined)
		section.rows[target.row]!.columns[target.column]!.field = field;
	else
		section.rows.splice(target.row, 0, { columns: [{ field, width: 12 }] });
	return next;
}
export function moveCanvasField(
	layout: FormLayout,
	field: string,
	target: CanvasTarget,
): FormLayout {
	const next = structuredClone(layout);
	let source: FormLayoutColumn | undefined;
	for (const s of next.sections)
		for (const r of s.rows)
			for (const c of r.columns) if (c.field === field) source = c;
	if (!source) return layout;
	if (target.column !== undefined) {
		const destination =
			next.sections[target.section]!.rows[target.row]!.columns[
				target.column
			]!;
		source.field = destination.field ?? null;
		destination.field = field;
	} else {
		source.field = null;
		if (!next.sections[target.section]) next.sections.push({ rows: [] });
		next.sections[target.section]!.rows.splice(target.row, 0, {
			columns: [{ field, width: 12 }],
		});
	}
	return next;
}
export function resizeCanvasRow(
	row: FormLayoutRow,
	preset: string,
): FormLayoutRow | null {
	const widths = ROW_PRESETS.find((p) => p.value === preset)!.widths;
	const fields = row.columns
		.map((c) => c.field)
		.filter((f): f is string => Boolean(f));
	if (fields.length > widths.length) return null;
	return {
		columns: widths.map((width, index) => ({
			field: fields[index] ?? null,
			width,
		})),
	};
}
export function moveCanvasRow(
	layout: FormLayout,
	source: CanvasTarget,
	target: CanvasTarget,
): FormLayout {
	const next = structuredClone(layout);
	const [row] = next.sections[source.section]!.rows.splice(source.row, 1);
	if (!row) return layout;
	const index =
		source.section === target.section && source.row < target.row
			? target.row - 1
			: target.row;
	next.sections[target.section]!.rows.splice(index, 0, row);
	return next;
}
export function moveCanvasSection(
	layout: FormLayout,
	source: number,
	target: number,
): FormLayout {
	const next = structuredClone(layout);
	const [section] = next.sections.splice(source, 1);
	if (!section) return layout;
	next.sections.splice(source < target ? target - 1 : target, 0, section);
	return next;
}
export function synchronizeFormLayout(
	layout: FormLayout | null | undefined,
	previous: FormField[],
	fields: FormField[],
): FormLayout | null | undefined {
	if (!layout) return layout;
	const names = new Set(fields.map((f) => f.name));
	const oldNames = new Set(previous.map((f) => f.name));
	const removed = previous.filter((f) => !names.has(f.name));
	const added = fields.filter((f) => !oldNames.has(f.name));
	const rename =
		previous.length === fields.length &&
		removed.length === 1 &&
		added.length === 1
			? new Map([[removed[0]!.name, added[0]!.name]])
			: new Map<string, string>();
	const placed = new Set<string>();
	const next = structuredClone(layout);
	for (const section of next.sections)
		for (const row of section.rows)
			for (const column of row.columns) {
				if (!column.field) continue;
				const name = rename.get(column.field) ?? column.field;
				column.field =
					names.has(name) && !placed.has(name) ? name : null;
				if (column.field) placed.add(column.field);
			}
	if (!next.sections.length) next.sections.push({ rows: [] });
	for (const field of fields)
		if (!placed.has(field.name))
			next.sections[0]!.rows.push({
				columns: [{ field: field.name, width: 12 }],
			});
	return next;
}
