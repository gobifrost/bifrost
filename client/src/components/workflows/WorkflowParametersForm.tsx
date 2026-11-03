import { useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { CodeEditor } from "@/components/tables/CodeEditor";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Play, Loader2 } from "lucide-react";
import type { components } from "@/lib/v1";
type WorkflowParameter = components["schemas"]["WorkflowParameter"];

interface WorkflowParametersFormProps {
	parameters: WorkflowParameter[];
	onExecute: (params: Record<string, unknown>) => void | Promise<void>;
	isExecuting?: boolean;
	showExecuteButton?: boolean;
	executeButtonText?: string;
	className?: string;
	// Controlled mode: pass values and onChange to lift state up
	values?: Record<string, unknown>;
	onChange?: (values: Record<string, unknown>) => void;
	// When true, renders a div instead of a form element (avoids nested forms)
	renderAsDiv?: boolean;
	// When true, don't set HTML required attribute on inputs (e.g. for optional input mapping)
	disableRequired?: boolean;
}

/** Keep draft text intact while the shared editor emits parsed parameter values. */
function StructuredParameterInput({
	value,
	onChange,
	label,
	readOnly,
}: {
	value: unknown;
	onChange: (value: unknown) => void;
	label: string;
	readOnly: boolean;
}) {
	const modelId = useId();
	const serialize = (next: unknown) =>
		typeof next === "string" ? next : (JSON.stringify(next, null, 2) ?? "");
	const [text, setText] = useState(() => serialize(value));
	const [lastValue, setLastValue] = useState(value);
	if (value !== lastValue) {
		setLastValue(value);
		setText(serialize(value));
	}
	return (
		<CodeEditor
			mode="json"
			path={`workflow-parameters/${modelId}/value.json`}
			ariaLabel={label}
			height="180px"
			readOnly={readOnly}
			text={text}
			onChange={(next) => {
				setText(next);
				let parsed: unknown = next;
				try {
					parsed = JSON.parse(next);
				} catch {
					/* Preserve incomplete JSON while editing. */
				}
				setLastValue(parsed);
				onChange(parsed);
			}}
		/>
	);
}

/**
 * Reusable form for entering workflow parameters
 * Used in both ExecuteWorkflow page and FormBuilder test launch workflow dialog
 */
export function WorkflowParametersForm({
	parameters,
	onExecute,
	isExecuting = false,
	showExecuteButton = true,
	executeButtonText = "Execute Workflow",
	className,
	values: controlledValues,
	onChange,
	renderAsDiv = false,
	disableRequired = false,
}: WorkflowParametersFormProps) {
	// Initialize internal state with default values (used when uncontrolled)
	const [internalValues, setInternalValues] = useState<
		Record<string, unknown>
	>(() =>
		parameters.reduce(
			(acc: Record<string, unknown>, param) => {
				acc[param.name] =
					param.default_value ?? (param.type === "bool" ? false : "");
				return acc;
			},
			{} as Record<string, unknown>,
		),
	);

	// Use controlled values if provided, otherwise use internal state
	const isControlled =
		controlledValues !== undefined && onChange !== undefined;
	const paramValues = isControlled ? controlledValues : internalValues;

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		await onExecute(paramValues);
	};

	const handleParameterChange = (paramName: string, value: unknown) => {
		const newValues = {
			...paramValues,
			[paramName]: value,
		};
		if (isControlled) {
			onChange(newValues);
		} else {
			setInternalValues(newValues);
		}
	};

	const renderParameterInput = (param: WorkflowParameter) => {
		const value = paramValues[param.name ?? ""];
		const displayName = param.label || param.name;

		// If parameter has options (e.g., from Literal types), render as dropdown
		if (param.options && param.options.length > 0) {
			return (
				<div className="min-w-0 space-y-2">
					<Label htmlFor={param.name ?? "select"}>
						{displayName}
						{param.required && (
							<span className="text-destructive ml-1">*</span>
						)}
					</Label>
					<Select
						value={(value as string) ?? ""}
						onValueChange={(newValue) =>
							handleParameterChange(param.name ?? "", newValue)
						}
						disabled={isExecuting}
					>
						<SelectTrigger
							className="w-full"
							id={param.name ?? "select"}
						>
							<SelectValue
								placeholder={
									param.default_value != null
										? `Default: ${param.default_value}`
										: "Select an option..."
								}
							/>
						</SelectTrigger>
						<SelectContent>
							{param.options.map((option) => (
								<SelectItem
									key={option.value}
									value={option.value}
								>
									{option.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					{param.description && (
						<p className="text-xs text-muted-foreground">
							{param.description}
						</p>
					)}
				</div>
			);
		}

		switch (param.type) {
			case "bool":
				return (
					<div className="flex min-h-14 items-center justify-between gap-4 rounded-[var(--bf-radius-surface)] border border-border bg-muted/50 p-3">
						<div className="min-w-0 space-y-0.5 [overflow-wrap:anywhere]">
							<Label
								htmlFor={param.name ?? "checkbox"}
								className="font-medium"
							>
								{displayName}
								{param.required && (
									<span className="text-destructive ml-1">
										*
									</span>
								)}
							</Label>
							{param.description && (
								<p className="text-xs text-muted-foreground">
									{param.description}
								</p>
							)}
						</div>
						<Checkbox
							id={param.name ?? "checkbox"}
							checked={!!value}
							onCheckedChange={(checked) =>
								handleParameterChange(param.name ?? "", checked)
							}
							disabled={isExecuting}
						/>
					</div>
				);

			case "int":
			case "float":
				return (
					<div className="min-w-0 space-y-2">
						<Label htmlFor={param.name ?? "number"}>
							{displayName}
							{param.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Input
							id={param.name ?? "number"}
							type="number"
							step={param.type === "float" ? "any" : "1"}
							value={(value as string | number | undefined) ?? ""}
							onChange={(e) =>
								handleParameterChange(
									param.name ?? "",
									e.target.value === ""
										? undefined
										: param.type === "int"
											? parseInt(e.target.value)
											: parseFloat(e.target.value),
								)
							}
							placeholder={
								param.default_value != null
									? `Default: ${param.default_value}`
									: undefined
							}
							required={!disableRequired && param.required}
							disabled={isExecuting}
						/>
						{param.description && (
							<p className="text-xs text-muted-foreground">
								{param.description}
							</p>
						)}
					</div>
				);

			case "json":
			case "dict":
			case "list":
				return (
					<div
						className="min-w-0 space-y-2"
						role="group"
						aria-label={displayName}
					>
						<Label>
							{displayName}{" "}
							<span className="text-muted-foreground text-xs">
								({param.type})
							</span>
							{param.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<StructuredParameterInput
							value={value}
							label={`${displayName} (${param.type})`}
							readOnly={isExecuting}
							onChange={(next) =>
								handleParameterChange(param.name, next)
							}
						/>
						{param.description && (
							<p className="text-xs text-muted-foreground">
								{param.description}
							</p>
						)}
					</div>
				);

			default:
				// string, email
				return (
					<div className="min-w-0 space-y-2">
						<Label htmlFor={param.name ?? "text"}>
							{displayName}
							{param.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Input
							id={param.name ?? "text"}
							type={param.type === "email" ? "email" : "text"}
							value={(value as string) ?? ""}
							onChange={(e) =>
								handleParameterChange(
									param.name ?? "",
									e.target.value || undefined,
								)
							}
							placeholder={
								param.default_value != null
									? `Default: ${param.default_value}`
									: undefined
							}
							required={!disableRequired && param.required}
							disabled={isExecuting}
						/>
						{param.description && (
							<p className="text-xs text-muted-foreground">
								{param.description}
							</p>
						)}
					</div>
				);
		}
	};

	if (parameters.length === 0) {
		return (
			<div className={className}>
				<p className="text-sm text-muted-foreground">
					This workflow has no parameters.
				</p>
				{showExecuteButton && (
					<Button
						type="button"
						className="w-full mt-4"
						disabled={isExecuting}
						onClick={() => onExecute({})}
					>
						{isExecuting ? (
							<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
						) : (
							<Play className="mr-2 h-4 w-4" />
						)}
						{isExecuting ? "Executing..." : executeButtonText}
					</Button>
				)}
			</div>
		);
	}

	const content = (
		<>
			<div className="space-y-4">
				{parameters.map((param) => (
					<div key={param.name ?? "param"}>
						{renderParameterInput(param)}
					</div>
				))}
			</div>

			{showExecuteButton && (
				<Button
					type="submit"
					className="w-full mt-6"
					disabled={isExecuting}
				>
					{isExecuting ? (
						<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
					) : (
						<Play className="mr-2 h-4 w-4" />
					)}
					{isExecuting ? "Executing..." : executeButtonText}
				</Button>
			)}
		</>
	);

	if (renderAsDiv) {
		return <div className={className}>{content}</div>;
	}

	return (
		<form onSubmit={handleSubmit} className={className}>
			{content}
		</form>
	);
}
