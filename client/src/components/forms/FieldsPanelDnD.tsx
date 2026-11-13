import { useState, useEffect, useRef } from "react";
import {
	Plus,
	Type,
	ChevronDown,
	Star,
	Workflow as WorkflowIcon,
} from "lucide-react";
import { FORM_COMPONENT_TYPES } from "./ComponentTypeBadge";
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
import { FieldConfigDialog } from "./FieldConfigDialog";
import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type WorkflowParameter = components["schemas"]["WorkflowParameter"];
import { draggable } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { FormCanvas, type CanvasSource } from "./FormCanvas";
import {
	createVerticalLayout,
	rootCanvasTarget,
	createCanvasRow,
	placeCanvasField,
	synchronizeFormLayout,
	type CanvasTarget,
	type FormCanvasState,
	type FormLayout,
} from "./form-layout";

interface FieldsPanelProps {
	fields: FormField[];
	layout: FormLayout | null | undefined;
	onChange: (state: FormCanvasState) => void;
	linkedWorkflow?: string;
	previewContext?: {
		workflow: Record<string, unknown>;
		query: Record<string, string>;
		field: Record<string, unknown>;
	};
}

// Field type templates for the palette
const FIELD_TEMPLATES = [
	"text",
	"email",
	"number",
	"select",
	"checkbox",
	"textarea",
	"radio",
	"datetime",
	"markdown",
	"html",
	"file",
].map((type) => ({ type, ...FORM_COMPONENT_TYPES[type]! }));

interface PaletteItemProps {
	template: (typeof FIELD_TEMPLATES)[0];
	onChoose: () => void;
}

function PaletteItem({ template, onChoose }: PaletteItemProps) {
	const ref = useRef<HTMLButtonElement>(null);
	const [dragging, setDragging] = useState(false);
	const { icon: Icon, color } = FORM_COMPONENT_TYPES[template.type]!;

	useEffect(() => {
		const el = ref.current;
		if (!el) return;

		return draggable({
			element: el,
			getInitialData: () =>
				template.type === "row" || template.type === "section"
					? { type: "canvas-component", component: template.type }
					: { type: "template", fieldType: template.type },
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
			<div className={`rounded-md border p-2 ${color}`}>
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
	const color =
		FORM_COMPONENT_TYPES[param.dataProvider ? "select" : fieldType]!.color;

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
			<div className={`rounded-md border p-2 ${color}`}>
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
	layout,
	onChange,
	linkedWorkflow,
	previewContext,
}: FieldsPanelProps) {
	const canvas = layout ?? createVerticalLayout(fields);
	const [paletteExpanded, setPaletteExpanded] = useState(false);
	const [reorderAnnouncement, setReorderAnnouncement] = useState("");
	const [selectedField, setSelectedField] = useState<FormField>();
	const [editingName, setEditingName] = useState<string>();
	const [deletingField, setDeletingField] = useState<FormField>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [newFieldType, setNewFieldType] = useState<string>();
	const [workflowInputData, setWorkflowInputData] = useState<{
		name: string;
		required: boolean;
		helpText?: string;
		dataProvider?: string;
	}>();
	const [isWorkflowInput, setIsWorkflowInput] = useState(false);
	const [pendingTarget, setPendingTarget] = useState<CanvasTarget>();
	const { data: metadata } = useWorkflowsMetadata() as {
		data?: { workflows?: WorkflowMetadata[]; dataProviders?: unknown[] };
	};
	const workflowParams = linkedWorkflow
		? (
				metadata?.workflows?.find((w) => w.id === linkedWorkflow)
					?.parameters ?? []
			).filter(
				(p: WorkflowParameter) =>
					!fields.some((f) => f.name === p.name),
			)
		: [];
	const rootTarget = () => rootCanvasTarget(canvas);
	const requestField = (source: CanvasSource, target: CanvasTarget) => {
		setSelectedField(undefined);
		setEditingName(undefined);
		setPendingTarget(target);
		setNewFieldType(
			typeof source.fieldType === "string" ? source.fieldType : undefined,
		);
		setIsWorkflowInput(source.type === "workflow-input");
		setWorkflowInputData(
			source.type === "workflow-input"
				? {
						name: String(source.fieldName ?? ""),
						required: Boolean(source.required),
						...(typeof source.description === "string"
							? { helpText: source.description }
							: {}),
						...(typeof source.dataProvider === "string"
							? { dataProvider: source.dataProvider }
							: {}),
					}
				: undefined,
		);
		setIsDialogOpen(true);
	};
	const chooseTemplate = (
		fieldType: string,
		param?: WorkflowInputItemProps["param"],
	) =>
		requestField(
			param
				? {
						type: "workflow-input",
						fieldType,
						fieldName: param.name,
						required: param.required,
						description: param.description ?? param.helpText,
						dataProvider: param.dataProvider,
					}
				: { type: "template", fieldType },
			rootTarget(),
		);
	const addComponent = (component: "row" | "section") => {
		const next = structuredClone(canvas);
		if (component === "section")
			next.sections.push({
				title: "Section",
				description: null,
				rows: [],
			});
		else {
			const target = rootCanvasTarget(next);
			if (!next.sections[target.section])
				next.sections.push({ rows: [] });
			next.sections[target.section]!.rows.push(createCanvasRow());
		}
		onChange({ fields, layout: next });
	};
	const handleSaveField = (field: FormField) => {
		const nextFields = editingName
			? fields.map((f) => (f.name === editingName ? field : f))
			: [...fields, field];
		const nextLayout = editingName
			? (synchronizeFormLayout(layout, fields, nextFields) ?? null)
			: placeCanvasField(canvas, field.name, pendingTarget!);
		onChange({ fields: nextFields, layout: nextLayout });
		setIsDialogOpen(false);
		setNewFieldType(undefined);
		setPendingTarget(undefined);
	};
	const handleConfirmDelete = () => {
		if (deletingField) {
			const nextFields = fields.filter(
				(f) => f.name !== deletingField.name,
			);
			onChange({
				fields: nextFields,
				layout:
					synchronizeFormLayout(layout, fields, nextFields) ?? null,
			});
		}
		setDeletingField(undefined);
		setIsDeleteDialogOpen(false);
	};
	return (
		<div className="flex min-h-0 w-full flex-col gap-4 lg:grid lg:h-full lg:grid-cols-[minmax(14rem,17rem)_minmax(0,1fr)]">
			<span role="status" className="sr-only">
				{reorderAnnouncement}
			</span>
			{/* Component Palette */}
			<Card className="flex shrink-0 flex-col overflow-hidden lg:h-full lg:min-h-0">
				<CardHeader className="pb-3 flex-shrink-0">
					<div className="flex items-center gap-2">
						<WorkflowIcon className="h-4 w-4 text-primary" />
						<CardTitle className="text-base">
							Component Palette
						</CardTitle>
					</div>
					<CardDescription className="text-xs">
						Choose a component or drag it onto the canvas
					</CardDescription>
					<Button
						variant="outline"
						size="sm"
						className="min-h-11 lg:hidden"
						aria-expanded={paletteExpanded}
						onClick={() => setPaletteExpanded(!paletteExpanded)}
					>
						{paletteExpanded
							? "Hide Component Palette"
							: "Choose a Component"}
					</Button>
				</CardHeader>
				<CardContent
					className={`${paletteExpanded ? "" : "hidden lg:block"} min-h-0 flex-1 space-y-4 overflow-y-auto max-h-72 sm:max-h-80 lg:max-h-none`}
				>
					<div className="space-y-2">
						<h4 className="text-xs font-semibold text-muted-foreground">
							Layout
						</h4>
						<PaletteItem
							template={{
								type: "row",
								...FORM_COMPONENT_TYPES.row!,
								label: "Row",
							}}
							onChoose={() => addComponent("row")}
						/>
						<PaletteItem
							template={{
								type: "section",
								...FORM_COMPONENT_TYPES.section!,
								label: "Section",
							}}
							onChoose={() => addComponent("section")}
						/>
					</div>
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

			<Card className="flex min-h-[28rem] min-w-0 flex-col overflow-hidden lg:h-full lg:min-h-0">
				<CardHeader className="shrink-0">
					<div className="flex items-start justify-between gap-2">
						<div>
							<CardTitle>Form Canvas</CardTitle>
							<CardDescription>
								Drag fields, rows, and sections here. Drop
								fields into the columns.
							</CardDescription>
						</div>
						<Button
							type="button"
							variant="outline"
							size="icon"
							aria-label="Add Field"
							onClick={() =>
								requestField({ type: "template" }, rootTarget())
							}
						>
							<Plus className="size-4" />
						</Button>
					</div>
				</CardHeader>
				<CardContent className="min-h-0 flex-1 overflow-y-auto">
					<FormCanvas
						fields={fields}
						layout={canvas}
						onChange={(next) => onChange({ fields, layout: next })}
						onRequestField={requestField}
						onEdit={(field) => {
							setSelectedField(field);
							setEditingName(field.name);
							setNewFieldType(undefined);
							setWorkflowInputData(undefined);
							setIsWorkflowInput(false);
							setIsDialogOpen(true);
						}}
						onDelete={(field) => {
							setDeletingField(field);
							setIsDeleteDialogOpen(true);
						}}
						onAnnounce={setReorderAnnouncement}
					/>
				</CardContent>
			</Card>
			<FieldConfigDialog
				field={selectedField ?? undefined}
				open={isDialogOpen}
				onClose={() => {
					setIsDialogOpen(false);
					setNewFieldType(undefined);
					setWorkflowInputData(undefined);
					setPendingTarget(undefined);
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
							{deletingField?.label ?? ""}
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
