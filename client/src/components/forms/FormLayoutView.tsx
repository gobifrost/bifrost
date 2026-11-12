import type { ReactNode } from "react";
import type { FormField } from "@/lib/client-types";
import { formLayoutWidthClass, type FormLayout } from "./form-layout";

/** The preview and every respondent surface share the same row/slot rendering. */
export function FormLayoutView({
	layout,
	fields,
	renderField,
}: {
	layout: FormLayout;
	fields: FormField[];
	renderField: (field: FormField) => ReactNode;
}) {
	const visible = new Map(fields.map((field) => [field.name, field]));
	return (
		<div className="space-y-6">
			{layout.sections.map((section, si) => {
				const rows = section.rows.filter(
					(row) =>
						(row.field && visible.has(row.field)) ||
						row.columns.some(
							(column) =>
								column.field && visible.has(column.field),
						),
				);
				if (!rows.length) return null;
				return (
					<section key={si} className="space-y-4">
						{(section.title || section.description) && (
							<div className="min-w-0 space-y-1">
								{section.title && (
									<h2 className="break-words text-lg font-semibold">
										{section.title}
									</h2>
								)}
								{section.description && (
									<p className="break-words text-sm text-muted-foreground">
										{section.description}
									</p>
								)}
							</div>
						)}
						{rows.map((row, ri) =>
							row.field ? (
								<div key={ri}>
									{renderField(visible.get(row.field)!)}
								</div>
							) : (
								<div
									key={ri}
									className="grid grid-cols-12 items-start gap-4"
								>
									{row.columns.map((column, ci) => {
										const field = column.field
											? visible.get(column.field)
											: undefined;
										return (
											<div
												key={ci}
												aria-hidden={
													field ? undefined : true
												}
												className={`${field ? "min-w-0" : "hidden md:block"} ${formLayoutWidthClass[column.width]}`}
											>
												{field
													? renderField(field)
													: null}
											</div>
										);
									})}
								</div>
							),
						)}
					</section>
				);
			})}
		</div>
	);
}
