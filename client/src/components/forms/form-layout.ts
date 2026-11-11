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
