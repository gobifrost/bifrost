import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";

type GeneratedFormLayout = components["schemas"]["FormLayout"];
type GeneratedFormLayoutSection = NonNullable<
	GeneratedFormLayout["sections"]
>[number];
type GeneratedFormLayoutRow = NonNullable<
	GeneratedFormLayoutSection["rows"]
>[number];
type GeneratedFormLayoutColumn = NonNullable<
	GeneratedFormLayoutRow["columns"]
>[number];

export const FORM_LAYOUT_WIDTHS = [3, 4, 6, 8, 9, 12] as const;

export type FormLayoutColumn = Omit<GeneratedFormLayoutColumn, "width"> & {
	width: (typeof FORM_LAYOUT_WIDTHS)[number];
};
export type FormLayoutRow = Omit<GeneratedFormLayoutRow, "columns"> & {
	columns: FormLayoutColumn[];
};
export type FormLayoutSection = Omit<GeneratedFormLayoutSection, "rows"> & {
	rows: FormLayoutRow[];
};
export type FormLayout = Omit<GeneratedFormLayout, "sections"> & {
	sections: FormLayoutSection[];
};

export type LayoutPosition = "before" | "after";

export interface LayoutRowLocation {
	section: number;
	row: number;
}

export interface LayoutColumnLocation extends LayoutRowLocation {
	field: string;
}

export const formLayoutWidthClass: Record<number, string> = {
	3: "col-span-12 md:col-span-3",
	4: "col-span-12 md:col-span-4",
	6: "col-span-12 md:col-span-6",
	8: "col-span-12 md:col-span-8",
	9: "col-span-12 md:col-span-9",
	12: "col-span-12",
};

/** Convert generated API types into the editor's always-addressable state. */
export function normalizeFormLayout(
	layout: GeneratedFormLayout | null | undefined,
): FormLayout | null {
	if (!layout) return null;
	return {
		...layout,
		sections: (layout.sections ?? []).map((section) => ({
			...section,
			rows: (section.rows ?? []).map((row) => ({
				...row,
				columns: (row.columns ?? []).map((column) => ({ ...column })),
			})),
		})),
	};
}

export function createVerticalLayout(fields: FormField[]): FormLayout {
	return {
		sections: [
			{
				rows: fields.map((field) => ({
					columns: [{ field: field.name, width: 12 }],
				})),
			},
		],
	};
}

/**
 * Keeps an opted-in layout valid after the field editor adds, deletes, or
 * renames fields. A form without a layout remains legacy/vertical.
 */
export function synchronizeFormLayout(
	layout: FormLayout | null | undefined,
	previousFields: FormField[],
	nextFields: FormField[],
): FormLayout | null | undefined {
	if (!layout) return layout;

	const previousNames = new Set(previousFields.map((field) => field.name));
	const nextNames = new Set(nextFields.map((field) => field.name));
	const removed = previousFields.filter(
		(field) => !nextNames.has(field.name),
	);
	const added = nextFields.filter((field) => !previousNames.has(field.name));
	const renamed =
		previousFields.length === nextFields.length &&
		removed.length === 1 &&
		added.length === 1
			? new Map([[removed[0]!.name, added[0]!.name]])
			: new Map<string, string>();

	const placed = new Set<string>();
	const sections = layout.sections.map((section) => ({
		...section,
		rows: section.rows.map((row) => ({
			...row,
			columns: row.columns.flatMap((column) => {
				const field = renamed.get(column.field) ?? column.field;
				if (!nextNames.has(field) || placed.has(field)) return [];
				placed.add(field);
				return [{ ...column, field }];
			}),
		})),
	}));

	const missing = nextFields.filter((field) => !placed.has(field.name));
	if (missing.length > 0) {
		const targetSection = sections[0] ?? { rows: [] };
		if (sections.length === 0) sections.push(targetSection);
		targetSection.rows.push(
			...missing.map((field) => ({
				columns: [{ field: field.name, width: 12 as const }],
			})),
		);
	}

	return { ...layout, sections };
}

export function moveLayoutSection(
	layout: FormLayout,
	sourceIndex: number,
	targetIndex: number,
	position: LayoutPosition,
): FormLayout {
	if (sourceIndex === targetIndex) return layout;
	const next = structuredClone(layout);
	const [section] = next.sections.splice(sourceIndex, 1);
	if (!section) return layout;
	const adjustedTarget =
		sourceIndex < targetIndex ? targetIndex - 1 : targetIndex;
	next.sections.splice(
		position === "after" ? adjustedTarget + 1 : adjustedTarget,
		0,
		section,
	);
	return next;
}

export function moveLayoutRow(
	layout: FormLayout,
	source: LayoutRowLocation,
	target: LayoutRowLocation,
	position: LayoutPosition,
): FormLayout {
	if (source.section === target.section && source.row === target.row)
		return layout;
	const next = structuredClone(layout);
	const [row] =
		next.sections[source.section]?.rows.splice(source.row, 1) ?? [];
	if (!row) return layout;
	const targetIndex =
		source.section === target.section && source.row < target.row
			? target.row - 1
			: target.row;
	const targetRows = next.sections[target.section]?.rows;
	if (!targetRows || !targetRows[targetIndex]) return layout;
	targetRows.splice(
		position === "after" ? targetIndex + 1 : targetIndex,
		0,
		row,
	);
	return next;
}

export function appendLayoutRow(
	layout: FormLayout,
	source: LayoutRowLocation,
	targetSection: number,
): FormLayout | null {
	if (source.section === targetSection) return layout;
	const next = structuredClone(layout);
	const [row] =
		next.sections[source.section]?.rows.splice(source.row, 1) ?? [];
	if (!row || !next.sections[targetSection]) return null;
	next.sections[targetSection]!.rows.push(row);
	return next;
}

export function moveLayoutColumn(
	layout: FormLayout,
	source: LayoutColumnLocation,
	target: LayoutColumnLocation,
	position: LayoutPosition,
): { layout: FormLayout | null; error?: string } {
	if (
		source.section === target.section &&
		source.row === target.row &&
		source.field === target.field
	) {
		return { layout };
	}
	const sourceRow = layout.sections[source.section]?.rows[source.row];
	const targetRow = layout.sections[target.section]?.rows[target.row];
	const sourceColumn = sourceRow?.columns.find(
		(column) => column.field === source.field,
	);
	if (!sourceRow || !targetRow || !sourceColumn) return { layout: null };
	const sameRow = sourceRow === targetRow;
	const targetWidth = targetRow.columns.reduce(
		(sum, column) => sum + column.width,
		0,
	);
	if (!sameRow && targetRow.columns.length >= 4) {
		return {
			layout: null,
			error: "A row can contain at most four fields.",
		};
	}
	if (
		targetWidth - (sameRow ? sourceColumn.width : 0) + sourceColumn.width >
		12
	) {
		return {
			layout: null,
			error: "That row does not have enough width for this field.",
		};
	}

	const next = structuredClone(layout);
	const nextSourceRow = next.sections[source.section]!.rows[source.row]!;
	const sourceIndex = nextSourceRow.columns.findIndex(
		(column) => column.field === source.field,
	);
	const [column] = nextSourceRow.columns.splice(sourceIndex, 1);
	const nextTargetRow = next.sections[target.section]!.rows[target.row]!;
	const targetIndex = nextTargetRow.columns.findIndex(
		(candidate) => candidate.field === target.field,
	);
	if (!column || targetIndex < 0) return { layout: null };
	nextTargetRow.columns.splice(
		position === "after" ? targetIndex + 1 : targetIndex,
		0,
		column,
	);
	return { layout: next };
}

export function appendLayoutColumn(
	layout: FormLayout,
	source: LayoutColumnLocation,
	target: LayoutRowLocation,
): { layout: FormLayout | null; error?: string } {
	const sourceRow = layout.sections[source.section]?.rows[source.row];
	const targetRow = layout.sections[target.section]?.rows[target.row];
	const sourceColumn = sourceRow?.columns.find(
		(column) => column.field === source.field,
	);
	if (!sourceRow || !targetRow || !sourceColumn) return { layout: null };
	if (sourceRow === targetRow) return { layout };
	if (targetRow.columns.length >= 4) {
		return {
			layout: null,
			error: "A row can contain at most four fields.",
		};
	}
	if (
		targetRow.columns.reduce((sum, column) => sum + column.width, 0) +
			sourceColumn.width >
		12
	) {
		return {
			layout: null,
			error: "That row does not have enough width for this field.",
		};
	}
	const next = structuredClone(layout);
	const nextSourceRow = next.sections[source.section]!.rows[source.row]!;
	const sourceIndex = nextSourceRow.columns.findIndex(
		(column) => column.field === source.field,
	);
	const [column] = nextSourceRow.columns.splice(sourceIndex, 1);
	if (!column) return { layout: null };
	next.sections[target.section]!.rows[target.row]!.columns.push(column);
	return { layout: next };
}
