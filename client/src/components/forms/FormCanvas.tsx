import { useEffect, useRef, useState, type ReactNode } from "react";
import {
	ArrowDown,
	ArrowUp,
	GripVertical,
	Pencil,
	Trash2,
	ArrowUpDown,
	Plus,
} from "lucide-react";
import {
	draggable,
	dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { autoScrollForElements } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/element";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSub,
	DropdownMenuSubTrigger,
	DropdownMenuSubContent,
} from "@/components/ui/dropdown-menu";
import type { FormField } from "@/lib/client-types";
import {
	createCanvasRow,
	formLayoutWidthClass,
	moveCanvasField,
	moveCanvasRow,
	moveCanvasSection,
	resizeCanvasRow,
	ROW_PRESETS,
	type CanvasTarget,
	type FormLayout,
} from "./form-layout";

export type CanvasSource = Record<string, unknown>;
type Target = CanvasTarget & {
	kind: "root" | "section" | "row" | "slot";
	after?: boolean;
};
const supported = (s: CanvasSource) =>
	[
		"template",
		"workflow-input",
		"canvas-field",
		"canvas-row",
		"canvas-section",
		"canvas-component",
	].includes(String(s.type));
function Drop({
	label,
	target,
	drag,
	onDrop,
	children,
	className = "",
}: {
	label: string;
	target: Target;
	drag?: CanvasSource;
	onDrop: (source: CanvasSource, target: Target) => void;
	children: (grip: ReactNode) => ReactNode;
	className?: string;
}) {
	const ref = useRef<HTMLDivElement>(null);
	const grip = useRef<HTMLButtonElement>(null);
	const [over, setOver] = useState<"before" | "after" | null>(null);
	useEffect(() => {
		const element = ref.current;
		if (!element) return;
		return combine(
			...(drag && grip.current
				? [
						draggable({
							element,
							dragHandle: grip.current,
							getInitialData: () => drag,
						}),
					]
				: []),
			dropTargetForElements({
				element,
				canDrop: ({ source }) =>
					supported(source.data) &&
					(target.kind === "slot"
						? [
								"template",
								"workflow-input",
								"canvas-field",
							].includes(String(source.data.type)) &&
							(!element.querySelector("[data-field-card]") ||
								source.data.type === "canvas-field")
						: target.kind === "row"
							? source.data.type !== "canvas-section" &&
								!(
									source.data.type === "canvas-component" &&
									source.data.component === "section"
								)
							: true),
				getData: ({ input }) => ({
					after:
						input.clientY >
						element.getBoundingClientRect().top +
							element.getBoundingClientRect().height / 2,
				}),
				onDragEnter: ({ self }) =>
					setOver(self.data.after ? "after" : "before"),
				onDrag: ({ self }) =>
					setOver(self.data.after ? "after" : "before"),
				onDragLeave: () => setOver(null),
				onDrop: ({ source, location, self }) => {
					setOver(null);
					if (location.current.dropTargets[0]?.element === element)
						onDrop(source.data, {
							...target,
							after: Boolean(self.data.after),
						});
				},
			}),
		);
	}, [drag, target, onDrop]);
	return (
		<div
			ref={ref}
			role={target.kind === "slot" ? "group" : "region"}
			aria-label={label}
			className={`relative ${className} ${over ? "ring-2 ring-primary/60" : ""}`}
		>
			{over && target.kind !== "slot" && (
				<div
					className={`pointer-events-none absolute inset-x-0 z-10 h-0.5 bg-primary ${over === "before" ? "top-0" : "bottom-0"}`}
				/>
			)}
			{children(
				drag ? (
					<Button
						ref={grip}
						type="button"
						variant="ghost"
						size="icon"
						className="size-9 shrink-0 cursor-grab active:cursor-grabbing"
						aria-label={`Drag ${label.toLowerCase()}`}
					>
						<GripVertical className="size-4" />
					</Button>
				) : null,
			)}
		</div>
	);
}
function FieldCard({
	field,
	onEdit,
	onDelete,
	onMove,
	onPlace,
	onRestoreFocus,
	destinations,
	canUp,
	canDown,
}: {
	field: FormField;
	onEdit: () => void;
	onDelete: () => void;
	onMove: (direction: -1 | 1) => void;
	onPlace: (target: CanvasTarget) => void;
	onRestoreFocus: (name: string) => void;
	destinations: Array<{ label: string; target: CanvasTarget }>;
	canUp: boolean;
	canDown: boolean;
}) {
	const ref = useRef<HTMLDivElement>(null);
	const grip = useRef<HTMLButtonElement>(null);
	const movedName = useRef<string | null>(null);
	useEffect(() => {
		if (!ref.current || !grip.current) return;
		return draggable({
			element: ref.current,
			dragHandle: grip.current,
			getInitialData: () => ({ type: "canvas-field", field: field.name }),
		});
	}, [field.name]);
	return (
		<div
			ref={ref}
			data-field-card={field.name}
			className="min-w-0 rounded border bg-background p-3"
		>
			<div className="flex items-start gap-1">
				<Button
					ref={grip}
					type="button"
					variant="ghost"
					size="icon"
					aria-label={`Drag field ${field.name}`}
					className="size-9 shrink-0 cursor-grab active:cursor-grabbing"
				>
					<GripVertical className="size-4" />
				</Button>
				<div className="min-w-0 flex-1 py-1">
					<p className="font-medium break-words">
						{field.label}
						{field.required ? " *" : ""}
					</p>
					<p className="font-mono text-xs text-muted-foreground break-all">
						{field.name}
					</p>
				</div>
			</div>
			<div
				aria-hidden
				className={`mt-2 min-h-10 rounded border border-border/70 px-3 py-2 text-sm text-muted-foreground ${field.type === "textarea" ? "min-h-20" : ""}`}
			>
				{field.placeholder ||
					(field.type === "checkbox"
						? "☐"
						: field.type === "select" ||
							  field.type === "multi_select"
							? "Select an option…"
							: field.type === "file"
								? "Choose a file…"
								: field.type === "markdown" ||
									  field.type === "html"
									? field.content
									: "")}
			</div>
			{field.help_text && (
				<p className="mt-2 text-xs text-muted-foreground break-words">
					{field.help_text}
				</p>
			)}
			<div className="mt-2 flex flex-wrap justify-end gap-1">
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							type="button"
							variant="ghost"
							size="icon"
							className="size-9"
							aria-label={`Move ${field.label}`}
						>
							<ArrowUpDown className="size-4" />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent
						onCloseAutoFocus={(event) => {
							if (movedName.current) {
								event.preventDefault();
								onRestoreFocus(movedName.current);
								movedName.current = null;
							}
						}}
					>
						<DropdownMenuItem
							disabled={!canUp}
							onSelect={() => {
								movedName.current = field.name;
								onMove(-1);
							}}
						>
							Move Up
						</DropdownMenuItem>
						<DropdownMenuItem
							disabled={!canDown}
							onSelect={() => {
								movedName.current = field.name;
								onMove(1);
							}}
						>
							Move Down
						</DropdownMenuItem>
						<DropdownMenuSub>
							<DropdownMenuSubTrigger>
								Move to Column
							</DropdownMenuSubTrigger>
							<DropdownMenuSubContent className="max-h-72 overflow-auto">
								{destinations.map((item) => (
									<DropdownMenuItem
										key={item.label}
										onSelect={() => {
											movedName.current = field.name;
											onPlace(item.target);
										}}
									>
										{item.label}
									</DropdownMenuItem>
								))}
							</DropdownMenuSubContent>
						</DropdownMenuSub>
					</DropdownMenuContent>
				</DropdownMenu>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-9"
					aria-label={`Edit ${field.label}`}
					onClick={onEdit}
				>
					<Pencil className="size-4" />
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-9"
					aria-label={`Delete ${field.label}`}
					onClick={onDelete}
				>
					<Trash2 className="size-4" />
				</Button>
			</div>
		</div>
	);
}
interface Props {
	fields: FormField[];
	layout: FormLayout;
	onChange: (layout: FormLayout) => void;
	onRequestField: (source: CanvasSource, target: CanvasTarget) => void;
	onEdit: (field: FormField) => void;
	onDelete: (field: FormField) => void;
	onAnnounce: (text: string) => void;
}
export function FormCanvas({
	fields,
	layout,
	onChange,
	onRequestField,
	onEdit,
	onDelete,
	onAnnounce,
}: Props) {
	const scrollRef = useRef<HTMLDivElement>(null);
	const restoreFocus = (name: string) => {
		const label = `Move ${fields.find((f) => f.name === name)?.label}`;
		Array.from(
			scrollRef.current?.querySelectorAll<HTMLButtonElement>("button") ??
				[],
		)
			.find((b) => b.getAttribute("aria-label") === label)
			?.focus();
	};
	useEffect(() => {
		let node = scrollRef.current?.parentElement;
		while (node) {
			if (/auto|scroll/.test(getComputedStyle(node).overflowY))
				return autoScrollForElements({ element: node });
			node = node.parentElement;
		}
		return undefined;
	}, []);
	const update = (fn: (next: FormLayout) => void) => {
		const next = structuredClone(layout);
		fn(next);
		onChange(next);
	};
	const fieldMap = new Map(fields.map((f) => [f.name, f]));
	const ordered = layout.sections.flatMap((s) =>
		s.rows.flatMap((r) =>
			r.columns.flatMap((c) => (c.field ? [c.field] : [])),
		),
	);
	const receive = (source: CanvasSource, target: Target) => {
		const next = structuredClone(layout);
		const insertion: CanvasTarget =
			target.kind === "slot"
				? target
				: target.kind === "row"
					? { ...target, row: target.row + (target.after ? 1 : 0) }
					: {
							section: target.section,
							row:
								next.sections[target.section]?.rows.length ?? 0,
						};
		if (source.type === "canvas-section") {
			onChange(
				moveCanvasSection(
					layout,
					Number(source.section),
					target.kind === "root"
						? layout.sections.length
						: target.section + (target.after ? 1 : 0),
				),
			);
			return;
		}
		if (
			source.type === "canvas-component" &&
			source.component === "section"
		) {
			next.sections.splice(
				target.kind === "root"
					? next.sections.length
					: target.section + (target.after ? 1 : 0),
				0,
				{ title: "Section", description: null, rows: [] },
			);
			onChange(next);
			return;
		}
		if (source.type === "canvas-row") {
			onChange(
				moveCanvasRow(
					layout,
					{
						section: Number(source.section),
						row: Number(source.row),
					},
					insertion,
				),
			);
			return;
		}
		if (source.type === "canvas-component") {
			if (!next.sections[insertion.section])
				next.sections.push({ rows: [] });
			next.sections[insertion.section]!.rows.splice(
				insertion.row,
				0,
				createCanvasRow(),
			);
			onChange(next);
			return;
		}
		if (source.type === "canvas-field") {
			if (
				insertion.column !== undefined &&
				layout.sections[insertion.section]!.rows[insertion.row]!
					.columns[insertion.column]!.field === source.field
			)
				return;
			onChange(moveCanvasField(layout, String(source.field), insertion));
			return;
		}
		onRequestField(source, insertion);
	};
	const moveField = (name: string, direction: -1 | 1) => {
		const index = ordered.indexOf(name),
			other = ordered[index + direction];
		if (!other) return;
		for (let section = 0; section < layout.sections.length; section++)
			for (
				let row = 0;
				row < layout.sections[section]!.rows.length;
				row++
			)
				for (
					let column = 0;
					column <
					layout.sections[section]!.rows[row]!.columns.length;
					column++
				)
					if (
						layout.sections[section]!.rows[row]!.columns[column]!
							.field === other
					) {
						onChange(
							moveCanvasField(layout, name, {
								section,
								row,
								column,
							}),
						);
						onAnnounce(
							`${fieldMap.get(name)?.label} moved to position ${index + direction + 1} of ${ordered.length}.`,
						);
						return;
					}
	};
	const lastSection = Math.max(0, layout.sections.length - 1);
	return (
		<div ref={scrollRef}>
			<Drop
				label="Form Canvas"
				target={{
					kind: "root",
					section: lastSection,
					row: layout.sections[lastSection]?.rows.length ?? 0,
				}}
				onDrop={receive}
				className="min-h-64 space-y-4 pb-16"
			>
				{() => (
					<>
						{layout.sections.map((section, si) => {
							const named =
								section.title !== undefined ||
								section.description !== undefined;
							return (
								<Drop
									key={si}
									label={`Section ${si + 1}`}
									target={{
										kind: "section",
										section: si,
										row: section.rows.length,
									}}
									{...(named
										? {
												drag: {
													type: "canvas-section",
													section: si,
												},
											}
										: {})}
									onDrop={receive}
									className={
										named
											? "space-y-3 rounded-lg border bg-muted/20 p-3"
											: "space-y-3"
									}
								>
									{(grip) => (
										<>
											{named && (
												<div className="flex flex-wrap items-center gap-2">
													{grip}
													<Input
														aria-label={`Section ${si + 1} Title`}
														value={
															section.title ?? ""
														}
														placeholder="Section Title"
														className="min-w-32 flex-1"
														onChange={(e) =>
															update((n) => {
																n.sections[
																	si
																]!.title =
																	e.target.value;
															})
														}
													/>
													<Button
														type="button"
														variant="ghost"
														size="icon"
														aria-label={`Move section ${si + 1} up`}
														disabled={si === 0}
														onClick={() =>
															onChange(
																moveCanvasSection(
																	layout,
																	si,
																	si - 1,
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
														aria-label={`Move section ${si + 1} down`}
														disabled={
															si ===
															layout.sections
																.length -
																1
														}
														onClick={() =>
															onChange(
																moveCanvasSection(
																	layout,
																	si,
																	si + 2,
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
														aria-label={`Remove section ${si + 1}`}
														disabled={section.rows.some(
															(r) =>
																r.columns.some(
																	(c) =>
																		c.field,
																),
														)}
														title="Move or delete fields before removing a section"
														onClick={() =>
															update((n) =>
																n.sections.splice(
																	si,
																	1,
																),
															)
														}
													>
														<Trash2 className="size-4" />
													</Button>
													<Input
														aria-label={`Section ${si + 1} Description`}
														value={
															section.description ??
															""
														}
														placeholder="Optional section description"
														onChange={(e) =>
															update((n) => {
																n.sections[
																	si
																]!.description =
																	e.target.value;
															})
														}
													/>
												</div>
											)}
											{section.rows.map((row, ri) => {
												const preset = row.columns
														.map((c) => c.width)
														.join(","),
													occupied =
														row.columns.filter(
															(c) => c.field,
														).length;
												return (
													<Drop
														key={ri}
														label={`Row ${ri + 1} in section ${si + 1}`}
														target={{
															kind: "row",
															section: si,
															row: ri,
														}}
														drag={{
															type: "canvas-row",
															section: si,
															row: ri,
														}}
														onDrop={receive}
														className="space-y-3 rounded-md border bg-card p-3"
													>
														{(rowGrip) => (
															<>
																<div className="flex flex-wrap items-center gap-2">
																	{rowGrip}
																	<span className="text-sm font-medium">
																		Row{" "}
																		{ri + 1}
																	</span>
																	<select
																		aria-label={`Columns for row ${ri + 1} in section ${si + 1}`}
																		className="h-9 rounded border bg-background px-2 text-sm"
																		value={
																			preset
																		}
																		onChange={(
																			e,
																		) => {
																			const resized =
																				resizeCanvasRow(
																					row,
																					e
																						.target
																						.value,
																				);
																			if (
																				resized
																			)
																				update(
																					(
																						n,
																					) => {
																						n.sections[
																							si
																						]!.rows[
																							ri
																						] =
																							resized;
																					},
																				);
																		}}
																	>
																		{!ROW_PRESETS.some(
																			(
																				p,
																			) =>
																				p.value ===
																				preset,
																		) && (
																			<option
																				value={
																					preset
																				}
																			>
																				Custom
																				Widths
																			</option>
																		)}
																		{ROW_PRESETS.map(
																			(
																				p,
																			) => (
																				<option
																					key={
																						p.value
																					}
																					value={
																						p.value
																					}
																					disabled={
																						occupied >
																						p
																							.widths
																							.length
																					}
																				>
																					{
																						p.label
																					}
																				</option>
																			),
																		)}
																	</select>
																	<span className="flex-1" />
																	<Button
																		type="button"
																		variant="ghost"
																		size="icon"
																		aria-label={`Move row ${ri + 1} in section ${si + 1} up`}
																		disabled={
																			ri ===
																			0
																		}
																		onClick={() =>
																			onChange(
																				moveCanvasRow(
																					layout,
																					{
																						section:
																							si,
																						row: ri,
																					},
																					{
																						section:
																							si,
																						row:
																							ri -
																							1,
																					},
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
																		aria-label={`Move row ${ri + 1} in section ${si + 1} down`}
																		disabled={
																			ri ===
																			section
																				.rows
																				.length -
																				1
																		}
																		onClick={() =>
																			onChange(
																				moveCanvasRow(
																					layout,
																					{
																						section:
																							si,
																						row: ri,
																					},
																					{
																						section:
																							si,
																						row:
																							ri +
																							2,
																					},
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
																		aria-label={`Remove row ${ri + 1} in section ${si + 1}`}
																		disabled={
																			occupied >
																			0
																		}
																		title="Move or delete fields before removing a row"
																		onClick={() =>
																			update(
																				(
																					n,
																				) =>
																					n.sections[
																						si
																					]!.rows.splice(
																						ri,
																						1,
																					),
																			)
																		}
																	>
																		<Trash2 className="size-4" />
																	</Button>
																</div>
																<div className="grid grid-cols-12 items-start gap-3">
																	{row.columns.map(
																		(
																			column,
																			ci,
																		) => {
																			const field =
																				column.field
																					? fieldMap.get(
																							column.field,
																						)
																					: undefined;
																			return (
																				<Drop
																					key={
																						ci
																					}
																					label={`Column ${ci + 1} of row ${ri + 1} in section ${si + 1}`}
																					target={{
																						kind: "slot",
																						section:
																							si,
																						row: ri,
																						column: ci,
																					}}
																					onDrop={
																						receive
																					}
																					className={`min-w-0 ${formLayoutWidthClass[column.width]}`}
																				>
																					{() =>
																						field ? (
																							<FieldCard
																								field={
																									field
																								}
																								onRestoreFocus={
																									restoreFocus
																								}
																								destinations={layout.sections.flatMap(
																									(
																										s,
																										section,
																									) =>
																										s.rows.flatMap(
																											(
																												r,
																												row,
																											) =>
																												r.columns.flatMap(
																													(
																														c,
																														column,
																													) =>
																														c.field ===
																														field.name
																															? []
																															: [
																																	{
																																		label: `Section ${section + 1}, Row ${row + 1}, Column ${column + 1}`,
																																		target: {
																																			section,
																																			row,
																																			column,
																																		},
																																	},
																																],
																												),
																										),
																								)}
																								onPlace={(
																									target,
																								) => {
																									onChange(
																										moveCanvasField(
																											layout,
																											field.name,
																											target,
																										),
																									);
																									onAnnounce(
																										`${field.label} moved to section ${target.section + 1}, row ${target.row + 1}, column ${target.column! + 1}.`,
																									);
																								}}

																								onEdit={() =>
																									onEdit(
																										field,
																									)
																								}
																								onDelete={() =>
																									onDelete(
																										field,
																									)
																								}
																								onMove={(
																									d,
																								) =>
																									moveField(
																										field.name,
																										d,
																									)
																								}
																								canUp={
																									ordered.indexOf(
																										field.name,
																									) >
																									0
																								}
																								canDown={
																									ordered.indexOf(
																										field.name,
																									) <
																									ordered.length -
																										1
																								}
																							/>
																						) : (
																							<button
																								type="button"
																								className="flex min-h-36 w-full flex-col items-center justify-center gap-2 rounded border-2 border-dashed p-4 text-center text-sm text-muted-foreground hover:border-primary hover:text-primary"
																								aria-label={`Add field to column ${ci + 1} of row ${ri + 1} in section ${si + 1}`}
																								onClick={() =>
																									onRequestField(
																										{
																											type: "template",
																										},
																										{
																											section:
																												si,
																											row: ri,
																											column: ci,
																										},
																									)
																								}
																							>
																								<Plus className="size-5" />
																								Drop
																								a
																								Field
																								Here
																								<span className="text-xs">
																									Or
																									click
																									to
																									add
																								</span>
																							</button>
																						)
																					}
																				</Drop>
																			);
																		},
																	)}
																</div>
															</>
														)}
													</Drop>
												);
											})}
											{named && !section.rows.length && (
												<button
													type="button"
													className="w-full rounded border-2 border-dashed p-6 text-sm text-muted-foreground"
													onClick={() =>
														update((n) =>
															n.sections[
																si
															]!.rows.push(
																createCanvasRow(),
															),
														)
													}
												>
													Drop a Row or Field Here
												</button>
											)}
										</>
									)}
								</Drop>
							);
						})}
						<div className="rounded-lg border-2 border-dashed p-6 text-center text-sm text-muted-foreground">
							<p className="font-medium">
								{fields.length ||
								layout.sections.some((s) => s.rows.length)
									? "Drop a Field, Row, or Section Here"
									: "Drop fields here"}
							</p>
							<p className="mt-1">
								Drag components from the palette, or choose one
								to add it.
							</p>
						</div>
					</>
				)}
			</Drop>
		</div>
	);
}
