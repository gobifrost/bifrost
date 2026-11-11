import { useEffect, useRef, useState, type ReactNode } from "react";
import {
	ArrowDown,
	ArrowLeft,
	ArrowRight,
	ArrowUp,
	GripVertical,
	Plus,
	X,
} from "lucide-react";
import {
	draggable,
	dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { autoScrollForElements } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/element";
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
	appendLayoutColumn,
	appendLayoutRow,
	createVerticalLayout,
	FORM_LAYOUT_WIDTHS,
	formLayoutWidthClass,
	moveLayoutColumn,
	moveLayoutRow,
	moveLayoutSection,
	type FormLayout,
	type LayoutColumnLocation,
	type LayoutPosition,
	type LayoutRowLocation,
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
function position(
	input: { clientY: number },
	element: Element,
): LayoutPosition {
	const rect = element.getBoundingClientRect();
	return input.clientY < rect.top + rect.height / 2 ? "before" : "after";
}
function columnPosition(
	input: { clientX: number; clientY: number },
	element: Element,
): LayoutPosition {
	const rect = element.getBoundingClientRect();
	return window.matchMedia("(min-width: 768px)").matches
		? input.clientX < rect.left + rect.width / 2
			? "before"
			: "after"
		: position(input, element);
}
function columnIndicatorClass(at: LayoutPosition) {
	return at === "before"
		? "left-1 right-1 top-0 h-0.5 md:bottom-1 md:left-0 md:right-auto md:top-1 md:h-auto md:w-0.5"
		: "bottom-0 left-1 right-1 h-0.5 md:bottom-1 md:left-auto md:right-0 md:top-1 md:h-auto md:w-0.5";
}
function Grip({
	ref,
	label,
}: {
	ref: React.RefObject<HTMLButtonElement | null>;
	label: string;
}) {
	return (
		<Button
			ref={ref}
			type="button"
			variant="ghost"
			size="icon"
			className="absolute right-3 top-3 cursor-grab touch-none active:cursor-grabbing"
			aria-label={label}
		>
			<GripVertical className="size-4" />
		</Button>
	);
}

function SectionDrop({
	index,
	onMove,
	children,
}: {
	index: number;
	onMove: (source: number, target: number, at: LayoutPosition) => void;
	children: ReactNode;
}) {
	const targetRef = useRef<HTMLElement>(null);
	const gripRef = useRef<HTMLButtonElement>(null);
	const [over, setOver] = useState<LayoutPosition | null>(null);
	useEffect(() => {
		const target = targetRef.current;
		const grip = gripRef.current;
		if (!target || !grip) return;
		return combine(
			draggable({
				element: target,
				dragHandle: grip,
				getInitialData: () => ({ type: "form-layout-section", index }),
			}),
			dropTargetForElements({
				element: target,
				getData: ({ input, element }) => ({
					position: position(input, element),
				}),
				canDrop: ({ source }) =>
					source.data.type === "form-layout-section" &&
					source.data.index !== index,
				onDragEnter: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDrag: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDragLeave: () => setOver(null),
				onDrop: ({ source, self }) => {
					setOver(null);
					onMove(
						source.data.index as number,
						index,
						self.data.position as LayoutPosition,
					);
				},
			}),
		);
	}, [index, onMove]);
	return (
		<section
			ref={targetRef}
			className={`relative space-y-4 rounded-lg border p-4 ${over ? "ring-2 ring-primary/50" : ""}`}
			aria-label={`Section ${index + 1}`}
		>
			{over && (
				<div
					className={`absolute left-3 right-3 h-0.5 bg-primary ${over === "before" ? "top-0" : "bottom-0"}`}
				/>
			)}
			<Grip ref={gripRef} label={`Drag section ${index + 1}`} />
			{children}
		</section>
	);
}
function RowDrop({
	location,
	onMove,
	children,
}: {
	location: LayoutRowLocation;
	onMove: (
		source: LayoutRowLocation,
		target: LayoutRowLocation,
		at: LayoutPosition,
	) => void;
	children: ReactNode;
}) {
	const targetRef = useRef<HTMLDivElement>(null);
	const gripRef = useRef<HTMLButtonElement>(null);
	const [over, setOver] = useState<LayoutPosition | null>(null);
	useEffect(() => {
		const target = targetRef.current;
		const grip = gripRef.current;
		if (!target || !grip) return;
		return combine(
			draggable({
				element: target,
				dragHandle: grip,
				getInitialData: () => ({
					type: "form-layout-row",
					...location,
				}),
			}),
			dropTargetForElements({
				element: target,
				getData: ({ input, element }) => ({
					position: position(input, element),
				}),
				canDrop: ({ source }) =>
					source.data.type === "form-layout-row" &&
					!(
						source.data.section === location.section &&
						source.data.row === location.row
					),
				onDragEnter: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDrag: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDragLeave: () => setOver(null),
				onDrop: ({ source, self }) => {
					setOver(null);
					onMove(
						{
							section: source.data.section as number,
							row: source.data.row as number,
						},
						location,
						self.data.position as LayoutPosition,
					);
				},
			}),
		);
	}, [location, onMove]);
	return (
		<div
			ref={targetRef}
			className={`relative rounded-md bg-muted/40 p-3 ${over ? "ring-2 ring-primary/50" : ""}`}
			aria-label={`Section ${location.section + 1} row ${location.row + 1}`}
		>
			{over && (
				<div
					className={`absolute left-3 right-3 h-0.5 bg-primary ${over === "before" ? "top-0" : "bottom-0"}`}
				/>
			)}
			<Grip
				ref={gripRef}
				label={`Drag row ${location.row + 1} in section ${location.section + 1}`}
			/>
			{children}
		</div>
	);
}
function ColumnDrop({
	location,
	onMove,
	children,
}: {
	location: LayoutColumnLocation;
	onMove: (
		source: LayoutColumnLocation,
		target: LayoutColumnLocation,
		at: LayoutPosition,
	) => void;
	children: ReactNode;
}) {
	const targetRef = useRef<HTMLDivElement>(null);
	const gripRef = useRef<HTMLButtonElement>(null);
	const [over, setOver] = useState<LayoutPosition | null>(null);
	useEffect(() => {
		const target = targetRef.current;
		const grip = gripRef.current;
		if (!target || !grip) return;
		return combine(
			draggable({
				element: target,
				dragHandle: grip,
				getInitialData: () => ({
					type: "form-layout-column",
					...location,
				}),
			}),
			dropTargetForElements({
				element: target,
				getData: ({ input, element }) => ({
					position: columnPosition(input, element),
				}),
				canDrop: ({ source }) =>
					source.data.type === "form-layout-column" &&
					source.data.field !== location.field,
				onDragEnter: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDrag: ({ self }) =>
					setOver(self.data.position as LayoutPosition),
				onDragLeave: () => setOver(null),
				onDrop: ({ source, self }) => {
					setOver(null);
					onMove(
						{
							section: source.data.section as number,
							row: source.data.row as number,
							field: source.data.field as string,
						},
						location,
						self.data.position as LayoutPosition,
					);
				},
			}),
		);
	}, [location, onMove]);
	return (
		<div
			ref={targetRef}
			role="group"
			aria-label={`Field placement ${location.field}`}
			className={`relative space-y-2 rounded border bg-background p-3 ${over ? "ring-2 ring-primary/50" : ""}`}
		>
			{over && (
				<div
					className={`absolute bg-primary ${columnIndicatorClass(over)}`}
				/>
			)}
			<Grip ref={gripRef} label={`Drag field ${location.field}`} />
			{children}
		</div>
	);
}
function FieldDropZone({
	location,
	onMove,
}: {
	location: LayoutRowLocation;
	onMove: (source: LayoutColumnLocation, target: LayoutRowLocation) => void;
}) {
	const ref = useRef<HTMLDivElement>(null);
	const [over, setOver] = useState(false);
	useEffect(() => {
		const element = ref.current;
		if (!element) return;
		return dropTargetForElements({
			element,
			canDrop: ({ source }) =>
				source.data.type === "form-layout-column" &&
				!(
					source.data.section === location.section &&
					source.data.row === location.row
				),
			onDragEnter: () => setOver(true),
			onDragLeave: () => setOver(false),
			onDrop: ({ source }) => {
				setOver(false);
				onMove(
					{
						section: source.data.section as number,
						row: source.data.row as number,
						field: source.data.field as string,
					},
					location,
				);
			},
		});
	}, [location, onMove]);
	return (
		<div
			ref={ref}
			aria-label={`Drop field into section ${location.section + 1} row ${location.row + 1}`}
			className={`col-span-12 rounded border border-dashed px-3 py-2 text-center text-xs text-muted-foreground ${over ? "border-primary bg-primary/10 text-primary" : ""}`}
		>
			Drop Field Here
		</div>
	);
}
function SectionRowDropZone({
	section,
	onMove,
}: {
	section: number;
	onMove: (source: LayoutRowLocation, targetSection: number) => void;
}) {
	const ref = useRef<HTMLDivElement>(null);
	const [over, setOver] = useState(false);
	useEffect(() => {
		const element = ref.current;
		if (!element) return;
		return dropTargetForElements({
			element,
			canDrop: ({ source }) =>
				source.data.type === "form-layout-row" &&
				source.data.section !== section,
			onDragEnter: () => setOver(true),
			onDragLeave: () => setOver(false),
			onDrop: ({ source }) => {
				setOver(false);
				onMove(
					{
						section: source.data.section as number,
						row: source.data.row as number,
					},
					section,
				);
			},
		});
	}, [section, onMove]);
	return (
		<div
			ref={ref}
			aria-label={`Drop row into section ${section + 1}`}
			className={`rounded border border-dashed px-3 py-2 text-center text-xs text-muted-foreground ${over ? "border-primary bg-primary/10 text-primary" : ""}`}
		>
			Drop Row Here
		</div>
	);
}

export function FormLayoutEditor({
	fields,
	layout,
	onChange,
}: FormLayoutEditorProps) {
	const [dragError, setDragError] = useState<string | null>(null);
	const scrollRef = useRef<HTMLDivElement>(null);
	const hasLayout = Boolean(layout);
	useEffect(() => {
		let element = scrollRef.current?.parentElement;
		while (element) {
			if (/(auto|scroll)/.test(getComputedStyle(element).overflowY)) {
				return autoScrollForElements({ element });
			}
			element = element.parentElement;
		}
		return undefined;
	}, [hasLayout]);
	if (!layout)
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
	const update = (mutate: (next: FormLayout) => void) => {
		const next = structuredClone(layout);
		mutate(next);
		onChange(next);
	};
	const swap = <T,>(items: T[], index: number, direction: -1 | 1) => {
		const target = index + direction;
		if (target >= 0 && target < items.length)
			[items[index], items[target]] = [items[target]!, items[index]!];
	};
	const moveField = (
		field: string,
		sectionIndex: number,
		rowIndex: number,
		columnIndex?: number,
	) =>
		update((next) => {
			let source: LayoutColumnLocation | undefined;
			next.sections.forEach((section, sectionIndex) =>
				section.rows.forEach((row, rowIndex) =>
					row.columns.forEach((column) => {
						if (column.field === field)
							source = {
								section: sectionIndex,
								row: rowIndex,
								field,
							};
					}),
				),
			);
			const target = next.sections[sectionIndex]!.rows[rowIndex]!;
			if (columnIndex === undefined) {
				const width = [...FORM_LAYOUT_WIDTHS]
					.reverse()
					.find(
						(candidate) =>
							candidate <=
							12 -
								target.columns.reduce(
									(sum, column) => sum + column.width,
									0,
								),
					);
				if (!width) return;
				if (source)
					next.sections[source.section]!.rows[source.row]!.columns =
						next.sections[source.section]!.rows[
							source.row
						]!.columns.filter((column) => column.field !== field);
				target.columns.push({ field, width });
				return;
			}
			const replacement = target.columns[columnIndex]!;
			if (source) {
				const sourceRow =
					next.sections[source.section]!.rows[source.row]!;
				const sourceIndex = sourceRow.columns.findIndex(
					(column) => column.field === field,
				);
				sourceRow.columns[sourceIndex] = {
					...sourceRow.columns[sourceIndex]!,
					field: replacement.field,
				};
			}
			target.columns[columnIndex] = { ...replacement, field };
		});
	const applyColumnMove = (
		result:
			| ReturnType<typeof moveLayoutColumn>
			| ReturnType<typeof appendLayoutColumn>,
	) => {
		if (result.layout) {
			setDragError(null);
			onChange(result.layout);
		} else
			setDragError(result.error ?? "This field cannot be moved there.");
	};
	return (
		<Card>
			<CardHeader>
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div>
						<CardTitle>Form Layout</CardTitle>
						<CardDescription>
							Drag the grips to change the preview presentation.
							The Fields list remains the field-definition editor;
							use arrows or menus without a pointer.
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
			<CardContent ref={scrollRef} className="space-y-5">
				{dragError && (
					<p
						role="alert"
						className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
					>
						{dragError}
					</p>
				)}
				{layout.sections.map((section, sectionIndex) => (
					<SectionDrop
						key={sectionIndex}
						index={sectionIndex}
						onMove={(source, target, at) =>
							onChange(
								moveLayoutSection(layout, source, target, at),
							)
						}
					>
						<div className="flex flex-wrap items-end gap-2 pr-10">
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
										swap(next.sections, sectionIndex, -1),
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
										swap(next.sections, sectionIndex, 1),
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
								<RowDrop
									key={rowIndex}
									location={{
										section: sectionIndex,
										row: rowIndex,
									}}
									onMove={(source, target, at) =>
										onChange(
											moveLayoutRow(
												layout,
												source,
												target,
												at,
											),
										)
									}
								>
									<div className="mb-3 flex flex-wrap items-center justify-between gap-2 pr-10">
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
														swap(
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
														swap(
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
													key={column.field}
													className={
														formLayoutWidthClass[
															column.width
														] ??
														formLayoutWidthClass[12]
													}
												>
													<ColumnDrop
														location={{
															section:
																sectionIndex,
															row: rowIndex,
															field: column.field,
														}}
														onMove={(
															source,
															target,
															at,
														) =>
															applyColumnMove(
																moveLayoutColumn(
																	layout,
																	source,
																	target,
																	at,
																),
															)
														}
													>
														<div className="pr-8">
															<Label
																htmlFor={`field-${sectionIndex}-${rowIndex}-${columnIndex}`}
															>
																Field
															</Label>
															<select
																id={`field-${sectionIndex}-${rowIndex}-${columnIndex}`}
																className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm"
																value={
																	column.field
																}
																onChange={(
																	event,
																) =>
																	moveField(
																		event
																			.target
																			.value,
																		sectionIndex,
																		rowIndex,
																		columnIndex,
																	)
																}
															>
																{fields
																	.filter(
																		(
																			field,
																		) =>
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
																	.map(
																		(
																			field,
																		) => (
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
																		),
																	)}
															</select>
														</div>
														<div>
															<Label
																htmlFor={`width-${sectionIndex}-${rowIndex}-${columnIndex}`}
															>
																Width
															</Label>
															<select
																id={`width-${sectionIndex}-${rowIndex}-${columnIndex}`}
																className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm"
																value={
																	column.width
																}
																onChange={(
																	event,
																) =>
																	update(
																		(
																			next,
																		) => {
																			const width =
																				Number(
																					event
																						.target
																						.value,
																				) as (typeof FORM_LAYOUT_WIDTHS)[number];
																			const targetRow =
																				next
																					.sections[
																					sectionIndex
																				]!
																					.rows[
																					rowIndex
																				]!;
																			if (
																				targetRow.columns.reduce(
																					(
																						sum,
																						item,
																					) =>
																						sum +
																						item.width,
																					0,
																				) -
																					targetRow
																						.columns[
																						columnIndex
																					]!
																						.width +
																					width <=
																				12
																			)
																				targetRow.columns[
																					columnIndex
																				]!.width =
																					width;
																		},
																	)
																}
															>
																{FORM_LAYOUT_WIDTHS.map(
																	(width) => (
																		<option
																			key={
																				width
																			}
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
														<div className="flex justify-end gap-1">
															<Button
																type="button"
																variant="ghost"
																size="icon"
																aria-label={`Move field ${column.field} left`}
																disabled={
																	columnIndex ===
																	0
																}
																onClick={() =>
																	update(
																		(
																			next,
																		) =>
																			swap(
																				next
																					.sections[
																					sectionIndex
																				]!
																					.rows[
																					rowIndex
																				]!
																					.columns,
																				columnIndex,
																				-1,
																			),
																	)
																}
															>
																<ArrowLeft className="size-4" />
															</Button>
															<Button
																type="button"
																variant="ghost"
																size="icon"
																aria-label={`Move field ${column.field} right`}
																disabled={
																	columnIndex ===
																	row.columns
																		.length -
																		1
																}
																onClick={() =>
																	update(
																		(
																			next,
																		) =>
																			swap(
																				next
																					.sections[
																					sectionIndex
																				]!
																					.rows[
																					rowIndex
																				]!
																					.columns,
																				columnIndex,
																				1,
																			),
																	)
																}
															>
																<ArrowRight className="size-4" />
															</Button>
														</div>
													</ColumnDrop>
												</div>
											),
										)}
										<FieldDropZone
											location={{
												section: sectionIndex,
												row: rowIndex,
											}}
											onMove={(source, target) =>
												applyColumnMove(
													appendLayoutColumn(
														layout,
														source,
														target,
													),
												)
											}
										/>
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
								</RowDrop>
							))}
							<SectionRowDropZone
								section={sectionIndex}
								onMove={(source, targetSection) => {
									const next = appendLayoutRow(
										layout,
										source,
										targetSection,
									);
									if (next) onChange(next);
								}}
							/>
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
					</SectionDrop>
				))}
			</CardContent>
		</Card>
	);
}
