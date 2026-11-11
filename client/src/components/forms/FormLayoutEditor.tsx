import { Plus, ArrowDown, ArrowUp, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { FormField } from "@/lib/client-types";
import {
	createVerticalLayout,
	FORM_LAYOUT_WIDTHS,
	formLayoutWidthClass,
	type FormLayout,
} from "./form-layout";

interface FormLayoutEditorProps {
	fields: FormField[];
	layout: FormLayout | null | undefined;
	onChange: (layout: FormLayout) => void;
}

function widthLabel(width: number) {
	return (
		{
			3: "Quarter",
			4: "Third",
			6: "Half",
			8: "Two Thirds",
			9: "Three Quarters",
			12: "Full",
		}[width] ?? `${width}/12`
	);
}

export function FormLayoutEditor({
	fields,
	layout,
	onChange,
}: FormLayoutEditorProps) {
	if (!layout) {
		return (
			<Card>
				<CardHeader>
					<CardTitle>Form Layout</CardTitle>
					<CardDescription>
						This form uses the classic vertical layout. Customize it
						to arrange fields into sections, rows, and columns.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<Button
						type="button"
						variant="outline"
						onClick={() => onChange(createVerticalLayout(fields))}
					>
						Customize Layout
					</Button>
				</CardContent>
			</Card>
		);
	}

	const update = (mutate: (next: FormLayout) => void) => {
		const next = structuredClone(layout);
		mutate(next);
		onChange(next);
	};
	const move = <T,>(items: T[], index: number, direction: -1 | 1) => {
		const nextIndex = index + direction;
		if (nextIndex < 0 || nextIndex >= items.length) return;
		[items[index], items[nextIndex]] = [items[nextIndex]!, items[index]!];
	};
	const moveField = (
		fieldName: string,
		targetSectionIndex: number,
		targetRowIndex: number,
		targetColumnIndex?: number,
	) => {
		update((next) => {
			let source:
				{ section: number; row: number; column: number } | undefined;
			next.sections.forEach((section, sectionIndex) =>
				section.rows.forEach((row, rowIndex) =>
					row.columns.forEach((column, columnIndex) => {
						if (column.field === fieldName)
							source = {
								section: sectionIndex,
								row: rowIndex,
								column: columnIndex,
							};
					}),
				),
			);
			const target =
				next.sections[targetSectionIndex]!.rows[targetRowIndex]!;
			if (targetColumnIndex === undefined) {
				const availableWidth = [...FORM_LAYOUT_WIDTHS]
					.reverse()
					.find(
						(width) =>
							width <=
							12 -
								target.columns.reduce(
									(sum, column) => sum + column.width,
									0,
								),
					);
				if (!availableWidth) return;
				if (source)
					next.sections[source.section]!.rows[
						source.row
					]!.columns.splice(source.column, 1);
				target.columns.push({
					field: fieldName,
					width: availableWidth,
				});
				return;
			}
			const replaced = target.columns[targetColumnIndex]!;
			if (source) {
				const sourceColumn =
					next.sections[source.section]!.rows[source.row]!.columns[
						source.column
					]!;
				next.sections[source.section]!.rows[source.row]!.columns[
					source.column
				] = {
					...sourceColumn,
					field: replaced.field,
				};
			}
			target.columns[targetColumnIndex] = {
				...replaced,
				field: fieldName,
			};
		});
	};

	return (
		<Card>
			<CardHeader>
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<CardTitle>Form Layout</CardTitle>
						<CardDescription>
							Arrange fields into sections and responsive rows.
							Use the arrows to adjust the order without a
							pointer.
						</CardDescription>
					</div>
					<Button
						type="button"
						variant="outline"
						onClick={() =>
							update((next) => next.sections.push({ rows: [] }))
						}
					>
						<Plus className="mr-2 size-4" /> Add Section
					</Button>
				</div>
			</CardHeader>
			<CardContent className="space-y-5">
				{layout.sections.map((section, sectionIndex) => (
					<section
						key={sectionIndex}
						className="space-y-4 rounded-lg border p-4"
						aria-label={`Section ${sectionIndex + 1}`}
					>
						<div className="flex flex-wrap items-end gap-2">
							<div className="min-w-52 flex-1 space-y-1">
								<Label
									htmlFor={`section-title-${sectionIndex}`}
								>
									Section Title
								</Label>
								<Input
									id={`section-title-${sectionIndex}`}
									value={section.title ?? ""}
									placeholder="Optional section title"
									onChange={(event) =>
										update((next) => {
											next.sections[sectionIndex]!.title =
												event.target.value || null;
										})
									}
								/>
							</div>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								aria-label={`Move section ${sectionIndex + 1} up`}
								disabled={sectionIndex === 0}
								onClick={() =>
									update((next) =>
										move(next.sections, sectionIndex, -1),
									)
								}
							>
								<ArrowUp className="size-4" />
							</Button>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								aria-label={`Move section ${sectionIndex + 1} down`}
								disabled={
									sectionIndex === layout.sections.length - 1
								}
								onClick={() =>
									update((next) =>
										move(next.sections, sectionIndex, 1),
									)
								}
							>
								<ArrowDown className="size-4" />
							</Button>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								aria-label={`Remove empty section ${sectionIndex + 1}`}
								disabled={section.rows.some(
									(row) => row.columns.length > 0,
								)}
								onClick={() =>
									update((next) =>
										next.sections.splice(sectionIndex, 1),
									)
								}
							>
								<X className="size-4" />
							</Button>
						</div>
						<div className="space-y-1">
							<Label
								htmlFor={`section-description-${sectionIndex}`}
							>
								Section Description
							</Label>
							<Input
								id={`section-description-${sectionIndex}`}
								value={section.description ?? ""}
								placeholder="Optional guidance for this section"
								onChange={(event) =>
									update((next) => {
										next.sections[
											sectionIndex
										]!.description =
											event.target.value || null;
									})
								}
							/>
						</div>
						<div className="space-y-3">
							{section.rows.map((row, rowIndex) => (
								<div
									key={rowIndex}
									className="rounded-md bg-muted/40 p-3"
									aria-label={`Section ${sectionIndex + 1} row ${rowIndex + 1}`}
								>
									<div className="mb-3 flex flex-wrap items-center justify-between gap-2">
										<p className="text-sm font-medium">
											Row {rowIndex + 1}
										</p>
										<div className="flex items-center gap-1">
											<Button
												type="button"
												variant="ghost"
												size="icon"
												aria-label={`Move row ${rowIndex + 1} up`}
												disabled={rowIndex === 0}
												onClick={() =>
													update((next) =>
														move(
															next.sections[
																sectionIndex
															]!.rows,
															rowIndex,
															-1,
														),
													)
												}
											>
												<ArrowUp className="size-4" />
											</Button>
											<Button
												type="button"
												variant="ghost"
												size="icon"
												aria-label={`Move row ${rowIndex + 1} down`}
												disabled={
													rowIndex ===
													section.rows.length - 1
												}
												onClick={() =>
													update((next) =>
														move(
															next.sections[
																sectionIndex
															]!.rows,
															rowIndex,
															1,
														),
													)
												}
											>
												<ArrowDown className="size-4" />
											</Button>
											<Button
												type="button"
												variant="ghost"
												size="icon"
												aria-label={`Remove empty row ${rowIndex + 1}`}
												disabled={
													row.columns.length > 0
												}
												onClick={() =>
													update((next) =>
														next.sections[
															sectionIndex
														]!.rows.splice(
															rowIndex,
															1,
														),
													)
												}
											>
												<X className="size-4" />
											</Button>
										</div>
									</div>
									<div className="grid grid-cols-1 gap-3 md:grid-cols-12">
										{row.columns.map(
											(column, columnIndex) => (
												<div
													key={`${column.field}-${columnIndex}`}
													className={`space-y-2 rounded border bg-background p-3 ${formLayoutWidthClass[column.width] ?? formLayoutWidthClass[12]}`}
												>
													<Label
														htmlFor={`field-${sectionIndex}-${rowIndex}-${columnIndex}`}
													>
														Field
													</Label>
													<select
														id={`field-${sectionIndex}-${rowIndex}-${columnIndex}`}
														className="h-9 w-full rounded-md border bg-background px-3 text-sm"
														value={column.field}
														onChange={(event) =>
															moveField(
																event.target
																	.value,
																sectionIndex,
																rowIndex,
																columnIndex,
															)
														}
													>
														{fields
															.filter(
																(field) =>
																	field.name ===
																		column.field ||
																	!row.columns.some(
																		(
																			item,
																		) =>
																			item.field ===
																			field.name,
																	),
															)
															.map((field) => (
																<option
																	key={
																		field.name
																	}
																	value={
																		field.name
																	}
																>
																	{field.label ??
																		field.name}
																</option>
															))}
													</select>
													<Label
														htmlFor={`width-${sectionIndex}-${rowIndex}-${columnIndex}`}
													>
														Width
													</Label>
													<select
														id={`width-${sectionIndex}-${rowIndex}-${columnIndex}`}
														className="h-9 w-full rounded-md border bg-background px-3 text-sm"
														value={column.width}
														onChange={(event) =>
															update((next) => {
																const width =
																	Number(
																		event
																			.target
																			.value,
																	) as (typeof FORM_LAYOUT_WIDTHS)[number];
																const row =
																	next
																		.sections[
																		sectionIndex
																	]!.rows[
																		rowIndex
																	]!;
																if (
																	row.columns.reduce(
																		(
																			sum,
																			item,
																		) =>
																			sum +
																			item.width,
																		0,
																	) -
																		row
																			.columns[
																			columnIndex
																		]!
																			.width +
																		width <=
																	12
																)
																	row.columns[
																		columnIndex
																	]!.width =
																		width;
															})
														}
													>
														{FORM_LAYOUT_WIDTHS.map(
															(width) => (
																<option
																	key={width}
																	value={
																		width
																	}
																	disabled={
																		row.columns.reduce(
																			(
																				sum,
																				item,
																			) =>
																				sum +
																				item.width,
																			0,
																		) -
																			column.width +
																			width >
																		12
																	}
																>
																	{widthLabel(
																		width,
																	)}
																</option>
															),
														)}
													</select>
												</div>
											),
										)}
										<select
											aria-label={`Add field to row ${rowIndex + 1}`}
											className="h-9 self-end rounded-md border bg-background px-3 text-sm md:col-span-3"
											value=""
											disabled={
												12 -
													row.columns.reduce(
														(sum, column) =>
															sum + column.width,
														0,
													) <
												3
											}
											onChange={(event) => {
												if (event.target.value)
													moveField(
														event.target.value,
														sectionIndex,
														rowIndex,
													);
											}}
										>
											<option value="">
												{12 -
													row.columns.reduce(
														(sum, column) =>
															sum + column.width,
														0,
													) <
												3
													? "Row is full"
													: "Move Field Here…"}
											</option>
											{fields
												.filter(
													(field) =>
														!row.columns.some(
															(column) =>
																column.field ===
																field.name,
														),
												)
												.map((field) => (
													<option
														key={field.name}
														value={field.name}
													>
														{field.label ??
															field.name}
													</option>
												))}
										</select>
									</div>
								</div>
							))}
						</div>
						<Button
							type="button"
							variant="outline"
							size="sm"
							onClick={() =>
								update((next) =>
									next.sections[sectionIndex]!.rows.push({
										columns: [],
									}),
								)
							}
						>
							<Plus className="mr-2 size-4" /> Add Row
						</Button>
					</section>
				))}
			</CardContent>
		</Card>
	);
}
