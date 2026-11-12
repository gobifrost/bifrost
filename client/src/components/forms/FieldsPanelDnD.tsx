import { useState, useEffect, useRef } from "react";
import {
	Pencil,
	Trash2,
	GripVertical,
	ArrowUpDown,
	Plus,
	Type,
	Mail,
	Hash,
	ChevronDown,
	CheckSquare,
	TextCursorInput,
	Star,
	Workflow as WorkflowIcon,
	CircleDot,
	Calendar,
	FileText,
	Code,
	Upload,
} from "lucide-react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
	DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { FieldConfigDialog } from "./FieldConfigDialog";
import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type WorkflowParameter = components["schemas"]["WorkflowParameter"];
import {
	draggable,
	dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { reorder } from "@atlaskit/pragmatic-drag-and-drop/reorder";
import { autoScrollForElements } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/element";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";

interface FieldsPanelProps {
	fields: FormField[];
	setFields: (fields: FormField[]) => void;
	linkedWorkflow?: string;
	previewContext?: {
		workflow: Record<string, unknown>;
		query: Record<string, string>;
		field: Record<string, unknown>;
	};
}

// Field type templates for the palette
const FIELD_TEMPLATES = [
	{ type: "text", icon: Type, label: "Text Input" },
	{ type: "email", icon: Mail, label: "Email" },
	{ type: "number", icon: Hash, label: "Number" },
	{
		type: "select",
		icon: ChevronDown,
		label: "Dropdown",
	},
	{
		type: "checkbox",
		icon: CheckSquare,
		label: "Checkbox",
	},
	{
		type: "textarea",
		icon: TextCursorInput,
		label: "Text Area",
	},
	{
		type: "radio",
		icon: CircleDot,
		label: "Radio Buttons",
	},
	{
		type: "datetime",
		icon: Calendar,
		label: "Date & Time",
	},
	{
		type: "markdown",
		icon: FileText,
		label: "Markdown",
	},
	{ type: "html", icon: Code, label: "HTML Content" },
	{ type: "file", icon: Upload, label: "File Upload" },
];

interface FieldItemProps {
	field: FormField;
	index: number;
	onEdit: () => void;
	onDelete: () => void;
	isDraggingNew: boolean;
	fieldCount: number;
	onMove: (direction: -1 | 1) => void;
}

function FieldItem({
	field,
	index,
	onEdit,
	onDelete,
	fieldCount,
	onMove,
}: FieldItemProps) {
	const ref = useRef<HTMLDivElement>(null);
	const [dragging, setDragging] = useState(false);
	const [isDraggedOver, setIsDraggedOver] = useState(false);
	const [dropPosition, setDropPosition] = useState<
		"none" | "before" | "after"
	>("none");

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return combine(
			draggable({
				element: el,
				getInitialData: () => ({ type: "field", index, field }),
				onDragStart: () => setDragging(true),
				onDrop: () => setDragging(false),
			}),
			dropTargetForElements({
				element: el,
				getData: ({ input, element }) => {
					const rect = element.getBoundingClientRect();
					const midpoint = rect.top + rect.height / 2;
					const position =
						input.clientY < midpoint ? "before" : "after";
					return { index, position };
				},
				canDrop: ({ source }) =>
					source.data["type"] === "field" ||
					source.data["type"] === "template" ||
					source.data["type"] === "workflow-input",
				onDragEnter: ({ source }) => {
					if (
						source.data["type"] === "template" ||
						source.data["type"] === "workflow-input"
					) {
						setIsDraggedOver(true);
					} else {
						setIsDraggedOver(true);
					}
				},
				onDrag: ({ self, source }) => {
					if (
						source.data["type"] === "template" ||
						source.data["type"] === "workflow-input"
					) {
						const position = self.data["position"] as
							"before" | "after";
						setDropPosition(position);
					}
				},
				onDragLeave: () => {
					setIsDraggedOver(false);
					setDropPosition("none");
				},
				onDrop: () => {
					setIsDraggedOver(false);
					setDropPosition("none");
				},
			}),
		);
	}, [index, field]);

	return (
		<div className="relative">
			{/* Drop indicator line */}
			{dropPosition === "before" && (
				<div className="absolute -top-1 left-0 right-0 h-0.5 bg-primary z-10" />
			)}

			<div
				ref={ref}
				className={`flex flex-wrap items-center gap-3 rounded-[var(--bf-radius-surface)] border p-3 transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none ${
					dragging ? "opacity-50" : ""
				} ${isDraggedOver ? "border-primary bg-accent" : "bg-card"} hover:border-primary/50 cursor-move`}
			>
				<GripVertical className="h-5 w-5 text-muted-foreground flex-shrink-0" />

				<div className="min-w-0 flex-1 basis-36">
					<div className="flex flex-wrap items-center gap-2">
						<p className="font-medium [overflow-wrap:anywhere]">
							{field.label}
						</p>
						{field.required && (
							<Badge variant="outline" className="text-xs">
								Required
							</Badge>
						)}
					</div>
					<div className="mt-1 flex items-center gap-2">
						<p className="font-mono text-xs text-muted-foreground truncate">
							{field.name}
						</p>
						<Badge
							variant="secondary"
							className="font-mono text-xs"
						>
							{field.type}
						</Badge>
					</div>
				</div>

				<div className="flex gap-1 flex-shrink-0">
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								variant="ghost"
								size="icon"
								className="size-11 sm:size-8"
								aria-label={`Move ${field.label}`}
							>
								<ArrowUpDown className="size-4" />
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="end">
							<DropdownMenuItem
								disabled={index === 0}
								onSelect={() => onMove(-1)}
							>
								Move up
							</DropdownMenuItem>
							<DropdownMenuItem
								disabled={index === fieldCount - 1}
								onSelect={() => onMove(1)}
							>
								Move down
							</DropdownMenuItem>
						</DropdownMenuContent>
					</DropdownMenu>
					<Button
						variant="ghost"
						size="icon"
						onClick={onEdit}
						aria-label={`Edit ${field.label}`}
						className="size-11 sm:size-8"
					>
						<Pencil className="h-4 w-4" />
					</Button>
					<Button
						variant="ghost"
						size="icon"
						onClick={onDelete}
						aria-label={`Delete ${field.label}`}
						className="size-11 sm:size-8"
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Drop indicator line after */}
			{dropPosition === "after" && (
				<div className="absolute -bottom-1 left-0 right-0 h-0.5 bg-primary z-10" />
			)}
		</div>
	);
}

interface PaletteItemProps {
	template: (typeof FIELD_TEMPLATES)[0];
	onChoose: () => void;
}

function PaletteItem({ template, onChoose }: PaletteItemProps) {
	const ref = useRef<HTMLButtonElement>(null);
	const [dragging, setDragging] = useState(false);
	const Icon = template.icon;

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return draggable({
			element: el,
			getInitialData: () => ({
				type: "template",
				fieldType: template.type,
			}),
			onDragStart: () => setDragging(true),
			onDrop: () => setDragging(false),
		});
	}, [template.type]);

	return (
		<button
			type="button"
			onClick={onChoose}
			ref={ref}
			className={`w-full text-left focus-visible:outline-2 focus-visible:outline-ring flex items-center gap-3 rounded-lg border p-3 cursor-grab active:cursor-grabbing transition-all hover:border-primary hover:shadow-sm ${
				dragging ? "opacity-50" : ""
			}`}
		>
				<div className="rounded-md bg-muted p-2 text-muted-foreground">
					<Icon className="h-4 w-4" />
				</div>
			<span className="text-sm font-medium">{template.label}</span>
		</button>
	);
}

interface WorkflowInputItemProps {
	onChoose: (fieldType: string) => void;
	param: {
		name?: string;
		type?: string;
		required?: boolean;
		label?: string | null;
		helpText?: string | null;
		description?: string | null;
		defaultValue?: unknown;
		dataProvider?: string | null;
	};
}

function WorkflowInputItem({ param, onChoose }: WorkflowInputItemProps) {
	const ref = useRef<HTMLButtonElement>(null);
	const [dragging, setDragging] = useState(false);

	// Map Python types to field types
	const getFieldType = (pythonType: string): string => {
		const typeMap: Record<string, string> = {
			str: "text",
			string: "text",
			int: "number",
			float: "number",
			number: "number",
			bool: "checkbox",
			boolean: "checkbox",
			email: "email",
			select: "select",
		};
		return typeMap[pythonType.toLowerCase()] || "text";
	};

	const fieldType = getFieldType(param.type ?? "text");
	const template = FIELD_TEMPLATES.find((t) => t.type === fieldType);
	const Icon = param.dataProvider ? ChevronDown : (template?.icon ?? Type);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return draggable({
			element: el,
			getInitialData: () => ({
				type: "workflow-input",
				fieldType,
				fieldName: param.name ?? "",
				required: param.required ?? false,
				description: param.description ?? param.helpText ?? undefined,
				dataProvider: param.dataProvider ?? undefined,
			}),
			onDragStart: () => setDragging(true),
			onDrop: () => setDragging(false),
		});
	}, [fieldType, param]);

	return (
		<button
			type="button"
			onClick={() => onChoose(fieldType)}
			ref={ref}
			className={`w-full text-left focus-visible:outline-2 focus-visible:outline-ring flex items-center gap-2 rounded-lg border border-primary/50 bg-primary/5 p-3 cursor-grab active:cursor-grabbing transition-all hover:border-primary hover:shadow-sm ${
				dragging ? "opacity-50" : ""
			}`}
		>
				<div className="rounded-md bg-muted p-2 text-muted-foreground">
					<Icon className="h-4 w-4" />
				</div>
			<div className="flex-1 min-w-0">
				<div className="flex items-center gap-1">
					<span className="text-xs font-mono font-semibold">
						{param.name ?? "unnamed"}
					</span>
					{param.required && (
						<Star className="h-3 w-3 text-warning fill-warning" />
					)}
				</div>
				{param.helpText && (
					<p className="text-[10px] text-muted-foreground truncate">
						{param.helpText}
					</p>
				)}
			</div>
		</button>
	);
}

export function FieldsPanelDnD({
	fields,
	setFields,
	linkedWorkflow,
	previewContext,
}: FieldsPanelProps) {
	const [paletteExpanded, setPaletteExpanded] = useState(false);
	const [reorderAnnouncement, setReorderAnnouncement] = useState("");
	const [selectedField, setSelectedField] = useState<FormField | undefined>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [editingIndex, setEditingIndex] = useState<number | undefined>();
	const [deletingIndex, setDeletingIndex] = useState<number | undefined>();
	const [newFieldType, setNewFieldType] = useState<string | undefined>();
	const [workflowInputData, setWorkflowInputData] = useState<{
		name: string;
		required: boolean;
		helpText?: string;
		dataProvider?: string;
	}>();
	const [insertAtIndex, setInsertAtIndex] = useState<number | undefined>();
	const [isDraggingNew, setIsDraggingNew] = useState(false);
	const [isWorkflowInput, setIsWorkflowInput] = useState(false);
	const dropZoneRef = useRef<HTMLDivElement>(null);

	const { data: metadata } = useWorkflowsMetadata() as {
		data?: { workflows?: WorkflowMetadata[]; dataProviders?: unknown[] };
	};

	// Get the current workflow's parameters, filtering out ones already in the form
	// linkedWorkflow is a workflow ID (UUID), not a name
	const workflowParams = linkedWorkflow
		? (
				metadata?.workflows?.find(
					(w: WorkflowMetadata) => w.id === linkedWorkflow,
				)?.parameters || []
			).filter(
				(param: WorkflowParameter) =>
					!fields.some((field) => field.name === param.name),
			)
		: [];

	useEffect(() => {
		const el = dropZoneRef.current;
		if (!el) return;

		return combine(
			dropTargetForElements({
				element: el,
				getData: () => ({ type: "dropzone" }),
				canDrop: ({ source }) =>
					source.data["type"] === "template" ||
					source.data["type"] === "field" ||
					source.data["type"] === "workflow-input",
				onDragStart: ({ source }) => {
					if (
						source.data["type"] === "template" ||
						source.data["type"] === "workflow-input"
					) {
						setIsDraggingNew(true);
					}
				},
				onDrop: ({ source, location }) => {
					setIsDraggingNew(false);

					if (source.data["type"] === "template") {
						// Dropped a template - determine insert index and open dialog
						const target = location.current.dropTargets.find(
							(t) => t.data["index"] !== undefined,
						);
						if (target?.data["index"] !== undefined) {
							const position = target.data["position"] as
								"before" | "after";
							const dropIndex =
								position === "before"
									? (target.data["index"] as number)
									: (target.data["index"] as number) + 1;
							setInsertAtIndex(dropIndex);
						} else {
							setInsertAtIndex(fields.length);
						}
						setNewFieldType(source.data["fieldType"] as string);
						setSelectedField(undefined);
						setEditingIndex(undefined);
						setWorkflowInputData(undefined);
						setIsWorkflowInput(false);
						setIsDialogOpen(true);
					} else if (source.data["type"] === "workflow-input") {
						// Dropped a workflow input - determine insert index and pre-fill field data
						const target = location.current.dropTargets.find(
							(t) => t.data["index"] !== undefined,
						);
						if (target?.data["index"] !== undefined) {
							const position = target.data["position"] as
								"before" | "after";
							const dropIndex =
								position === "before"
									? (target.data["index"] as number)
									: (target.data["index"] as number) + 1;
							setInsertAtIndex(dropIndex);
						} else {
							setInsertAtIndex(fields.length);
						}
						setNewFieldType(source.data["fieldType"] as string);
						setSelectedField(undefined);
						setEditingIndex(undefined);
						const description = source.data["description"] as
							string | undefined;
						const dataProvider = source.data["dataProvider"] as
							string | undefined;
						setWorkflowInputData({
							name: source.data["fieldName"] as string,
							required: source.data["required"] as boolean,
							...(description !== undefined && {
								helpText: description,
							}),
							...(dataProvider !== undefined && { dataProvider }),
						});
						setIsWorkflowInput(true);
						setIsDialogOpen(true);
					} else if (source.data["type"] === "field") {
						// Reordering existing fields
						const startIndex = source.data["index"] as number;
						const target = location.current.dropTargets[0];
						if (target?.data["index"] !== undefined) {
							const endIndex = target.data["index"] as number;
							if (startIndex !== endIndex) {
								setFields(
									reorder({
										list: fields,
										startIndex,
										finishIndex: endIndex,
									}),
								);
							}
						}
					}
				},
			}),
			autoScrollForElements({
				element: el,
			}),
		);
	}, [fields, setFields]);

	const handleEditField = (index: number) => {
		setSelectedField(fields[index]);
		setEditingIndex(index);
		setNewFieldType(undefined);
		setWorkflowInputData(undefined);
		setIsWorkflowInput(false);
		setIsDialogOpen(true);
	};

	const handleSaveField = (field: FormField) => {
		if (editingIndex !== undefined) {
			// Update existing field
			const newFields = [...fields];
			newFields[editingIndex] = field;
			setFields(newFields);
		} else {
			// Add new field at the specified index (or at the end if not specified)
			const newFields = [...fields];
			const index =
				insertAtIndex !== undefined ? insertAtIndex : fields.length;
			newFields.splice(index, 0, field);
			setFields(newFields);
		}
		setIsDialogOpen(false);
		setNewFieldType(undefined);
		setInsertAtIndex(undefined);
	};

	const handleDeleteField = (index: number) => {
		setDeletingIndex(index);
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = () => {
		if (deletingIndex !== undefined) {
			setFields(fields.filter((_, i) => i !== deletingIndex));
		}
		setIsDeleteDialogOpen(false);
		setDeletingIndex(undefined);
	};

	const chooseTemplate = (
		fieldType: string,
		parameter?: WorkflowInputItemProps["param"],
	) => {
		setNewFieldType(fieldType);
		setSelectedField(undefined);
		setEditingIndex(undefined);
		setInsertAtIndex(fields.length);
		setWorkflowInputData(
			parameter
				? {
						name: parameter.name ?? "",
						required: parameter.required ?? false,
						helpText:
							parameter.description ??
							parameter.helpText ??
							undefined,
						dataProvider: parameter.dataProvider ?? undefined,
					}
				: undefined,
		);
		setIsWorkflowInput(Boolean(parameter));
		setIsDialogOpen(true);
	};

	return (
		<div className="flex min-h-0 w-full flex-col gap-4 lg:grid lg:h-full lg:grid-cols-[minmax(16rem,20rem)_minmax(0,1fr)]">
			<span role="status" className="sr-only">
				{reorderAnnouncement}
			</span>
			{/* Field Palette */}
			<Card className="flex shrink-0 flex-col overflow-hidden lg:h-full lg:min-h-0">
				<CardHeader className="pb-3 flex-shrink-0">
					<div className="flex items-center gap-2">
						<WorkflowIcon className="h-4 w-4 text-primary" />
						<CardTitle className="text-base">
							Field Palette
						</CardTitle>
					</div>
					<CardDescription className="text-xs">
						Choose a field or drag it into the form
					</CardDescription>
					<Button
						variant="outline"
						size="sm"
						className="min-h-11 lg:hidden"
						aria-expanded={paletteExpanded}
						onClick={() => setPaletteExpanded(!paletteExpanded)}
					>
						{paletteExpanded
							? "Hide field palette"
							: "Choose a field"}
					</Button>
				</CardHeader>
				<CardContent
					className={`${paletteExpanded ? "" : "hidden lg:block"} min-h-0 flex-1 space-y-4 overflow-y-auto max-h-72 sm:max-h-80 lg:max-h-none`}
				>
					{/* Workflow Inputs Section */}
					{workflowParams.length > 0 && (
						<div className="space-y-2">
							<div className="flex items-center gap-1 mb-2">
								<h4 className="text-xs font-semibold uppercase tracking-wide text-primary">
									Workflow Inputs
								</h4>
								<Star className="h-3 w-3 text-warning fill-warning" />
							</div>
							<p className="text-[10px] text-muted-foreground mb-2">
								Required inputs are marked by{" "}
								<Star className="h-2.5 w-2.5 text-warning fill-warning inline" />
							</p>
							<div className="space-y-2">
								{workflowParams.map(
									(
										param: WorkflowParameter,
										index: number,
									) => (
										<WorkflowInputItem
											key={
												param.name ??
												`workflow-param-${index}`
											}
											param={param}
											onChoose={(type) =>
												chooseTemplate(type, param)
											}
										/>
									),
								)}
							</div>
						</div>
					)}

					{/* Divider */}
					{workflowParams.length > 0 && (
						<div className="relative">
							<div className="absolute inset-0 flex items-center">
								<span className="w-full border-t" />
							</div>
							<div className="relative flex justify-center text-xs uppercase">
								<span className="bg-card px-2 text-muted-foreground">
									or
								</span>
							</div>
						</div>
					)}

					{/* All Field Types Section */}
					<div className="space-y-2">
						<div className="flex items-center gap-1 mb-2">
							<h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
								All Field Types
							</h4>
						</div>
						<p className="text-[10px] text-muted-foreground mb-2">
							Additional fields not used by workflow
						</p>
						<div className="space-y-2">
							{FIELD_TEMPLATES.map((template) => (
								<PaletteItem
									key={template.type}
									template={template}
									onChoose={() =>
										chooseTemplate(template.type)
									}
								/>
							))}
						</div>
					</div>
				</CardContent>
			</Card>

			{/* Drop Zone */}
			<Card className="flex min-h-[28rem] flex-1 flex-col overflow-hidden lg:h-full lg:min-h-0">
				<CardHeader className="flex-shrink-0">
					<div className="flex items-center justify-between">
						<div>
							<CardTitle>Form Fields</CardTitle>
							<CardDescription>
								Drag fields or use their move menu to reorder
							</CardDescription>
						</div>
						<Button
							onClick={() => {
								setNewFieldType(undefined);
								setSelectedField(undefined);
								setEditingIndex(undefined);
								setInsertAtIndex(undefined);
								setIsWorkflowInput(false);
								setIsDialogOpen(true);
							}}
							variant="outline"
							size="icon"
							title="Add Field"
							aria-label="Add Field"
							className="size-11 sm:size-10"
						>
							<Plus className="h-4 w-4" />
						</Button>
					</div>
				</CardHeader>
				<CardContent className="min-h-0 flex-1 overflow-y-auto">
					<div ref={dropZoneRef} className="min-h-full">
						{fields.length > 0 ? (
							<div className="space-y-2">
								{fields.map((field, index) => (
									<FieldItem
										key={field.name}
										field={field}
										index={index}
										fieldCount={fields.length}
										onMove={(direction) => {
											const target = index + direction;
											if (
												target < 0 ||
												target >= fields.length
											)
												return;
											setFields(
												reorder({
													list: fields,
													startIndex: index,
													finishIndex: target,
												}),
											);
											setReorderAnnouncement(
												`${field.label} moved to position ${target + 1} of ${fields.length}.`,
											);
										}}
										onEdit={() => handleEditField(index)}
										onDelete={() =>
											handleDeleteField(index)
										}
										isDraggingNew={isDraggingNew}
									/>
								))}
							</div>
						) : (
							<div className="flex flex-col items-center justify-center py-16 text-center border-2 border-dashed rounded-lg">
								<div className="max-w-sm">
									<h3 className="text-lg font-semibold mb-2">
										Drop fields here
									</h3>
									<p className="text-sm text-muted-foreground">
										Drag field types from the left palette
										or click the + button to get started
									</p>
								</div>
							</div>
						)}
					</div>
				</CardContent>
			</Card>

			<FieldConfigDialog
				field={selectedField ?? undefined}
				open={isDialogOpen}
				onClose={() => {
					setIsDialogOpen(false);
					setNewFieldType(undefined);
					setWorkflowInputData(undefined);
					setInsertAtIndex(undefined);
					setIsWorkflowInput(false);
				}}
				onSave={handleSaveField}
				allFieldNames={fields.map((f) => f.name)}
				{...(previewContext && { previewContext })}
				{...(newFieldType && { defaultType: newFieldType })}
				{...(workflowInputData && { workflowInputData })}
				{...(isWorkflowInput && { isWorkflowInput })}
			/>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Remove Field</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to remove the field "
							{deletingIndex !== undefined
								? fields[deletingIndex]?.label
								: ""}
							"? This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Remove Field
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
