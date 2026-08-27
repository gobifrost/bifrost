/**
 * DynamicConfigForm - Renders dynamic config fields based on adapter config_schema.
 *
 * Parses JSON Schema with x-dynamic-values extensions (similar to Power Automate's
 * x-ms-dynamic-values pattern) to render dropdowns populated from API calls.
 *
 * Features:
 * - Renders fields based on JSON Schema type and properties
 * - Supports x-dynamic-values for API-populated dropdowns
 * - Handles cascading dependencies via depends_on
 * - Falls back to text input on error or for manual entry
 */

import { useEffect, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { AlertCircle, ChevronDown, Info, RefreshCw } from "lucide-react";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useDynamicValues } from "@/services/events";

// x-dynamic-values extension in JSON Schema
interface DynamicValuesSpec {
	operation: string;
	value_path: string;
	label_path: string;
	depends_on: string[];
}

// Property from JSON Schema
export interface SchemaProperty {
	type: string;
	title?: string;
	description?: string;
	default?: unknown;
	enum?: string[];
	items?: { type: string; enum?: string[] };
	"x-dynamic-values"?: DynamicValuesSpec;
	[key: string]: unknown; // Allow additional JSON Schema properties
}

// x-help extension for rich help content on fields
interface HelpSpec {
	text: string;
	code?: string;
}

/**
 * FieldLabel - Label with optional info popover for help content.
 *
 * Renders a label with a required marker and an (i) icon that opens
 * a popover with help text and optional code example.
 */
function FieldLabel({
	htmlFor,
	title,
	isRequired,
	help,
}: {
	htmlFor?: string;
	title: string;
	isRequired?: boolean;
	help?: HelpSpec;
}) {
	return (
		<div className="flex items-center gap-1.5">
			<Label htmlFor={htmlFor}>
				{title}
				{isRequired && <span className="text-destructive"> *</span>}
			</Label>
			{help && (
				<Tooltip>
					<TooltipTrigger asChild>
						<button
							type="button"
							className="text-muted-foreground hover:text-foreground transition-colors"
							onClick={(e) => e.preventDefault()}
						>
							<Info className="h-3.5 w-3.5" />
						</button>
					</TooltipTrigger>
					<TooltipContent
						side="right"
						className="max-w-xs text-left space-y-2 p-3"
					>
						<p>{help.text}</p>
						{help.code && (
							<pre className="bg-background/20 rounded-md px-2 py-1 text-[10px] font-mono whitespace-pre overflow-x-auto">
								{help.code}
							</pre>
						)}
					</TooltipContent>
				</Tooltip>
			)}
		</div>
	);
}

// Config schema structure
export interface ConfigSchema {
	type: string;
	required?: string[];
	properties?: Record<string, SchemaProperty>;
}

interface DynamicConfigFormProps {
	adapterName: string;
	integrationId?: string;
	requiresIntegration?: boolean;
	organizationId?: string | null;
	configSchema: ConfigSchema;
	config: Record<string, unknown>;
	onChange: (config: Record<string, unknown>) => void;
}

/**
 * Get nested value from object using dot notation path
 */
function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
	return path.split(".").reduce((acc: unknown, key) => {
		if (acc && typeof acc === "object" && key in acc) {
			return (acc as Record<string, unknown>)[key];
		}
		return undefined;
	}, obj);
}

/**
 * Build dependency graph for fields
 */
function buildDependencyOrder(
	properties: Record<string, SchemaProperty>,
): string[] {
	const visited = new Set<string>();
	const result: string[] = [];

	function visit(field: string) {
		if (visited.has(field)) return;
		visited.add(field);

		const prop = properties[field];
		if (prop?.["x-dynamic-values"]?.depends_on) {
			for (const dep of prop["x-dynamic-values"].depends_on) {
				if (properties[dep]) {
					visit(dep);
				}
			}
		}

		result.push(field);
	}

	for (const field of Object.keys(properties)) {
		visit(field);
	}

	return result;
}

/**
 * Dynamic field component that handles x-dynamic-values
 */
function DynamicField({
	fieldName,
	property,
	value,
	adapterName,
	integrationId,
	requiresIntegration,
	organizationId,
	currentConfig,
	onChange,
	isRequired,
}: {
	fieldName: string;
	property: SchemaProperty;
	value: unknown;
	adapterName: string;
	integrationId?: string;
	requiresIntegration?: boolean;
	organizationId?: string | null;
	currentConfig: Record<string, unknown>;
	onChange: (value: unknown) => void;
	isRequired: boolean;
}) {
	const [manualMode, setManualMode] = useState(false);
	const [open, setOpen] = useState(false);

	const dynamicSpec = property["x-dynamic-values"];
	const help = property["x-help"] as HelpSpec | undefined;

	// Check if dependencies are satisfied
	const dependenciesSatisfied = useMemo(() => {
		if (!dynamicSpec?.depends_on?.length) return true;
		return dynamicSpec.depends_on.every(
			(dep) =>
				currentConfig[dep] !== undefined && currentConfig[dep] !== "",
		);
	}, [dynamicSpec, currentConfig]);

	// Fetch dynamic values
	const {
		data: dynamicData,
		isLoading,
		error,
		refetch,
		isFetching,
	} = useDynamicValues(
		adapterName,
		dynamicSpec?.operation,
		integrationId,
		organizationId,
		currentConfig,
		!!dynamicSpec &&
			dependenciesSatisfied &&
			!manualMode &&
			(!requiresIntegration || !!integrationId),
	);
	const errorMessage =
		error instanceof Error
			? error.message
			: "We could not load options for this field.";

	// Handle array enum types (like change_types)
	if (property.type === "array" && property.items?.enum) {
		const arrayValue = (value as string[]) || property.default || [];

		return (
			<div className="space-y-2">
				<FieldLabel
					title={property.title || fieldName}
					isRequired={isRequired}
					help={help}
				/>
				<ToggleGroup
					type="multiple"
					value={arrayValue as string[]}
					onValueChange={(val) =>
						onChange(val.length > 0 ? val : undefined)
					}
					className="justify-start flex-wrap"
				>
					{property.items.enum.map((option) => (
						<ToggleGroupItem key={option} value={option} size="sm">
							{option}
						</ToggleGroupItem>
					))}
				</ToggleGroup>
				{property.description && (
					<p className="text-xs text-muted-foreground">
						{property.description}
					</p>
				)}
			</div>
		);
	}

	// Handle boolean type
	if (property.type === "boolean") {
		const boolValue = value !== undefined ? !!value : !!property.default;

		return (
			<div className="flex items-start space-x-3">
				<Checkbox
					id={fieldName}
					checked={boolValue}
					onCheckedChange={(checked) => onChange(checked)}
				/>
				<div className="space-y-1">
					<div className="flex items-center gap-1.5">
						<Label htmlFor={fieldName} className="cursor-pointer">
							{property.title || fieldName}
						</Label>
						{help && (
							<Tooltip>
								<TooltipTrigger asChild>
									<button
										type="button"
										className="text-muted-foreground hover:text-foreground transition-colors"
										onClick={(e) => e.preventDefault()}
									>
										<Info className="h-3.5 w-3.5" />
									</button>
								</TooltipTrigger>
								<TooltipContent
									side="right"
									className="max-w-xs text-left space-y-2 p-3"
								>
									<p>{help.text}</p>
									{help.code && (
										<pre className="bg-background/20 rounded-md px-2 py-1 text-[10px] font-mono whitespace-pre overflow-x-auto">
											{help.code}
										</pre>
									)}
								</TooltipContent>
							</Tooltip>
						)}
					</div>
					{property.description && (
						<p className="text-xs text-muted-foreground">
							{property.description}
						</p>
					)}
				</div>
			</div>
		);
	}

	// Handle static enum type
	if (property.enum && !dynamicSpec) {
		return (
			<div className="space-y-2">
				<FieldLabel
					htmlFor={fieldName}
					title={property.title || fieldName}
					isRequired={isRequired}
					help={help}
				/>
				<Select
					value={(value as string) || ""}
					onValueChange={(val) => onChange(val || undefined)}
				>
					<SelectTrigger id={fieldName} className="w-full">
						<SelectValue
							placeholder={`Select ${property.title || fieldName}...`}
						/>
					</SelectTrigger>
					<SelectContent>
						{property.enum.map((option) => (
							<SelectItem key={option} value={option}>
								{option}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
				{property.description && (
					<p className="text-xs text-muted-foreground">
						{property.description}
					</p>
				)}
			</div>
		);
	}

	// Handle dynamic values field
	if (dynamicSpec) {
		// Show dependency message if not satisfied
		if (!dependenciesSatisfied) {
			return (
				<div className="space-y-2">
					<FieldLabel
						htmlFor={fieldName}
						title={property.title || fieldName}
						isRequired={isRequired}
						help={help}
					/>
					<Input
						id={fieldName}
						disabled
						placeholder={`Select ${dynamicSpec.depends_on.join(", ")} first...`}
					/>
					{property.description && (
						<p className="text-xs text-muted-foreground">
							{property.description}
						</p>
					)}
				</div>
			);
		}

		// Show loading state
		if (isLoading) {
			return (
				<div className="space-y-2">
					<FieldLabel
						title={property.title || fieldName}
						help={help}
					/>
					<Skeleton className="h-10 w-full" />
				</div>
			);
		}

		// Show error with manual fallback
		if (error || manualMode) {
			return (
				<div className="space-y-2">
					<div className="flex items-center justify-between gap-3">
						<FieldLabel
							htmlFor={fieldName}
							title={property.title || fieldName}
							isRequired={isRequired}
							help={help}
						/>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-7 px-2 text-xs text-muted-foreground"
							onClick={() => setManualMode((current) => !current)}
						>
							{manualMode ? "Use list" : "Enter manually"}
						</Button>
					</div>
					<div className="flex items-center gap-2">
						<Input
							id={fieldName}
							value={(value as string) || ""}
							onChange={(e) =>
								onChange(e.target.value || undefined)
							}
							placeholder={
								property.description ||
								`Enter ${property.title || fieldName}...`
							}
							className={error ? "border-amber-500" : ""}
						/>
						{error && (
							<AlertCircle className="h-4 w-4 text-amber-500 flex-shrink-0" />
						)}
					</div>
					{error && (
						<div
							className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"
							role="status"
							aria-live="polite"
						>
							<div className="flex items-start gap-2">
								<AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
								<div className="min-w-0 flex-1 space-y-2">
									<p className="font-medium">
										Options did not load.
									</p>
									<p className="break-words">
										{errorMessage}
									</p>
									<div className="flex flex-wrap gap-2">
										<Button
											type="button"
											variant="outline"
											size="sm"
											className="h-7 px-2 text-xs"
											onClick={() => void refetch()}
											disabled={isFetching}
										>
											<RefreshCw
												className={cn(
													"mr-1.5 h-3 w-3",
													isFetching &&
														"animate-spin",
												)}
											/>
											Retry
										</Button>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											className="h-7 px-2 text-xs"
											onClick={() => setManualMode(true)}
										>
											Enter manually
										</Button>
									</div>
								</div>
							</div>
						</div>
					)}
				</div>
			);
		}

		// Render combobox with dynamic options
		const options = dynamicData?.items || [];
		const valuePath = dynamicSpec.value_path;
		const labelPath = dynamicSpec.label_path;

		const selectedOption = options.find(
			(opt) => getNestedValue(opt, valuePath) === value,
		);
		const selectedLabel = selectedOption
			? String(getNestedValue(selectedOption, labelPath))
			: undefined;

		return (
			<div className="space-y-2">
				<div className="flex items-center justify-between">
					<FieldLabel
						title={property.title || fieldName}
						isRequired={isRequired}
						help={help}
					/>
					<button
						type="button"
						className="text-xs text-muted-foreground hover:underline"
						onClick={() => setManualMode(true)}
					>
						Enter manually
					</button>
				</div>
				<Popover open={open} onOpenChange={setOpen}>
					<PopoverTrigger asChild>
						<Button
							variant="outline"
							role="combobox"
							aria-expanded={open}
							className="w-full justify-between font-normal"
						>
							<span
								className={cn(
									!selectedLabel && "text-muted-foreground",
								)}
							>
								{selectedLabel ||
									`Select ${property.title || fieldName}...`}
							</span>
							<ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
						</Button>
					</PopoverTrigger>
					<PopoverContent
						className="w-[var(--radix-popover-trigger-width)] max-w-[calc(100vw-2rem)] p-0"
						align="start"
					>
						<Command>
							<CommandInput
								placeholder={`Search ${property.title || fieldName}...`}
							/>
							<CommandList>
								<CommandEmpty>No options found.</CommandEmpty>
								<CommandGroup>
									{options.map((option, idx) => {
										const optValue = getNestedValue(
											option,
											valuePath,
										);
										const optLabel = String(
											getNestedValue(option, labelPath) ??
												"",
										);
										const optDesc =
											typeof option.description ===
											"string"
												? option.description
												: undefined;

										return (
											<CommandItem
												key={
													String(optValue) ||
													String(idx)
												}
												value={optLabel}
												data-checked={
													optValue === value
												}
												onSelect={() => {
													onChange(optValue);
													setOpen(false);
												}}
											>
												<div className="flex flex-col">
													<span>{optLabel}</span>
													{optDesc && (
														<span className="text-xs text-muted-foreground">
															{optDesc}
														</span>
													)}
												</div>
											</CommandItem>
										);
									})}
								</CommandGroup>
							</CommandList>
						</Command>
					</PopoverContent>
				</Popover>
				{property.description && (
					<p className="text-xs text-muted-foreground">
						{property.description}
					</p>
				)}
			</div>
		);
	}

	// Default: text input
	const isPassword = property.format === "password";

	return (
		<div className="space-y-2">
			<FieldLabel
				htmlFor={fieldName}
				title={property.title || fieldName}
				isRequired={isRequired}
				help={help}
			/>
			<Input
				id={fieldName}
				type={isPassword ? "password" : "text"}
				value={(value as string) || ""}
				onChange={(e) => onChange(e.target.value || undefined)}
				placeholder={
					property.description ||
					`Enter ${property.title || fieldName}...`
				}
			/>
			{property.description && (
				<p className="text-xs text-muted-foreground">
					{property.description}
				</p>
			)}
		</div>
	);
}

/**
 * Main DynamicConfigForm component
 */
export function DynamicConfigForm({
	adapterName,
	integrationId,
	requiresIntegration = false,
	organizationId,
	configSchema,
	config,
	onChange,
}: DynamicConfigFormProps) {
	// Memoize properties to prevent re-renders
	const properties = useMemo(
		() => configSchema.properties || {},
		[configSchema.properties],
	);
	const required = useMemo(
		() => configSchema.required || [],
		[configSchema.required],
	);

	// Build dependency order for rendering
	const fieldOrder = useMemo(
		() => buildDependencyOrder(properties),
		[properties],
	);

	// Clear dependent fields when parent changes
	useEffect(() => {
		const newConfig = { ...config };
		let hasChanges = false;

		for (const field of fieldOrder) {
			const prop = properties[field];
			if (prop?.["x-dynamic-values"]?.depends_on) {
				for (const dep of prop["x-dynamic-values"].depends_on) {
					// If parent changed and current field has a value, clear it
					if (config[dep] !== undefined) {
						// Check if any parent dependency is undefined/empty
						const parentMissing = prop[
							"x-dynamic-values"
						].depends_on.some(
							(d) => config[d] === undefined || config[d] === "",
						);
						if (parentMissing && config[field] !== undefined) {
							newConfig[field] = undefined;
							hasChanges = true;
						}
					}
				}
			}
		}

		if (hasChanges) {
			onChange(newConfig);
		}
	}, [config, fieldOrder, properties, onChange]);

	const handleFieldChange = (fieldName: string, value: unknown) => {
		const newConfig = { ...config };

		if (value === undefined || value === "") {
			delete newConfig[fieldName];

			// Clear dependent fields
			for (const [field, prop] of Object.entries(properties)) {
				if (prop["x-dynamic-values"]?.depends_on?.includes(fieldName)) {
					delete newConfig[field];
				}
			}
		} else {
			newConfig[fieldName] = value;
		}

		onChange(newConfig);
	};

	if (Object.keys(properties).length === 0) {
		return null;
	}

	return (
		<div className="space-y-4">
			{fieldOrder.map((fieldName) => {
				const property = properties[fieldName];
				if (!property) return null;

				return (
					<DynamicField
						key={fieldName}
						fieldName={fieldName}
						property={property}
						value={config[fieldName]}
						adapterName={adapterName}
						integrationId={integrationId}
						requiresIntegration={requiresIntegration}
						organizationId={organizationId}
						currentConfig={config}
						onChange={(value) =>
							handleFieldChange(fieldName, value)
						}
						isRequired={required.includes(fieldName)}
					/>
				);
			})}
		</div>
	);
}
