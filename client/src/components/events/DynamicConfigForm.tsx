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

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	AlertCircle,
	CheckCircle2,
	ChevronDown,
	Info,
	RefreshCw,
} from "lucide-react";
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
import { cn } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api-error";
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
	largeTarget = false,
}: {
	htmlFor?: string;
	title: string;
	isRequired?: boolean;
	help?: HelpSpec;
	largeTarget?: boolean;
}) {
	return (
		<div className="flex min-w-0 items-center gap-1.5">
			<Label
				htmlFor={htmlFor}
				className={cn(
					"min-w-0 flex-wrap leading-6 [overflow-wrap:anywhere]",
					largeTarget && "min-h-11 cursor-pointer",
				)}
			>
				{title}
				{isRequired && (
					<span className="text-destructive" aria-hidden="true">
						{" "}
						*
					</span>
				)}
				{isRequired && <span className="sr-only"> (required)</span>}
			</Label>
			{help && (
				<Popover>
					<PopoverTrigger asChild>
						<Button
							type="button"
							variant="ghost"
							className="size-11 shrink-0 p-0"
							aria-label={`Help for ${title}`}
						>
							<Info className="size-4" />
						</Button>
					</PopoverTrigger>
					<PopoverContent
						align="start"
						collisionPadding={16}
						className="max-h-[var(--radix-popover-content-available-height)] w-80 max-w-[calc(100vw-2rem)] space-y-3 overflow-auto text-sm leading-6 [overflow-wrap:anywhere]"
						aria-label={`Help for ${title}`}
					>
						<p>{help.text}</p>
						{help.code && (
							<pre
								tabIndex={0}
								aria-label={`${title} example`}
								className="max-h-60 overflow-auto whitespace-pre-wrap rounded-[var(--bf-radius-surface)] border bg-muted p-3 font-mono text-sm leading-6 [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							>
								{help.code}
							</pre>
						)}
					</PopoverContent>
				</Popover>
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
	/**
	 * A secret is already stored for this source. Password fields are
	 * write-only: the server never returns the value, and leaving the field
	 * blank keeps the stored secret.
	 */
	secretConfigured?: boolean;
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

function hasConfigValue(value: unknown) {
	return value !== undefined && value !== null && value !== "";
}

/** Remove downstream selections when their option source changes. */
function clearDependentValues(
	config: Record<string, unknown>,
	properties: Record<string, SchemaProperty>,
	changedField: string,
) {
	const visited = new Set([changedField]);
	const pending = [changedField];
	while (pending.length) {
		const parent = pending.shift()!;
		for (const [field, property] of Object.entries(properties)) {
			if (
				!visited.has(field) &&
				property["x-dynamic-values"]?.depends_on?.includes(parent)
			) {
				visited.add(field);
				pending.push(field);
				delete config[field];
			}
		}
	}
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
	secretConfigured = false,
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
	secretConfigured?: boolean;
}) {
	const fieldId = useId();
	const descriptionId = `${fieldId}-description`;
	const [manualMode, setManualMode] = useState(false);
	const [open, setOpen] = useState(false);

	const dynamicSpec = property["x-dynamic-values"];
	const help = property["x-help"] as HelpSpec | undefined;

	// Check if dependencies are satisfied
	const dependenciesSatisfied = useMemo(() => {
		if (!dynamicSpec?.depends_on?.length) return true;
		return dynamicSpec.depends_on.every((dep) =>
			hasConfigValue(currentConfig[dep]),
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
	const errorMessage = getErrorMessage(
		error,
		"We could not load options for this field.",
	);

	// Handle array enum types (like change_types)
	if (property.type === "array" && property.items?.enum) {
		const arrayValue =
			(value as string[] | undefined) ?? property.default ?? [];

		return (
			<div className="min-w-0 space-y-2">
				<FieldLabel
					title={property.title || fieldName}
					isRequired={isRequired}
					help={help}
				/>
				<ToggleGroup
					type="multiple"
					value={arrayValue as string[]}
					onValueChange={onChange}
					aria-label={property.title || fieldName}
					className="max-w-full flex-wrap justify-start gap-2"
				>
					{property.items.enum.map((option) => (
						<ToggleGroupItem
							key={option}
							value={option}
							size="sm"
							className="h-auto min-h-11 max-w-full whitespace-normal py-2 [overflow-wrap:anywhere]"
						>
							{option}
						</ToggleGroupItem>
					))}
				</ToggleGroup>
				{property.description && (
					<p
						id={descriptionId}
						className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
					>
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
			<div className="flex min-w-0 items-start gap-3">
				<Switch
					className="mt-3 shrink-0"
					aria-describedby={
						property.description ? descriptionId : undefined
					}
					id={fieldId}
					checked={boolValue}
					onCheckedChange={(checked) => onChange(checked)}
				/>
				<div className="min-w-0 space-y-1">
					<FieldLabel
						htmlFor={fieldId}
						title={property.title || fieldName}
						help={help}
						largeTarget
					/>
					{property.description && (
						<p
							id={descriptionId}
							className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
						>
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
			<div className="min-w-0 space-y-2">
				<FieldLabel
					htmlFor={fieldId}
					title={property.title || fieldName}
					isRequired={isRequired}
					help={help}
				/>
				<Select
					value={(value as string) ?? ""}
					onValueChange={(val) => onChange(val || undefined)}
				>
					<SelectTrigger
						aria-required={isRequired}
						id={fieldId}
						aria-describedby={
							property.description ? descriptionId : undefined
						}
						className="min-h-11 w-full data-[size=default]:h-auto py-2 [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:text-left [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
					>
						<SelectValue
							placeholder={`Select ${property.title || fieldName}...`}
						/>
					</SelectTrigger>
					<SelectContent>
						{property.enum.map((option) => (
							<SelectItem
								key={option}
								value={option}
								className="min-h-11 [overflow-wrap:anywhere]"
							>
								{option}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
				{property.description && (
					<p
						id={descriptionId}
						className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
					>
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
				<div className="min-w-0 space-y-2">
					<FieldLabel
						htmlFor={fieldId}
						title={property.title || fieldName}
						isRequired={isRequired}
						help={help}
					/>
					<Input
						aria-required={isRequired}
						aria-describedby={
							property.description ? descriptionId : undefined
						}
						id={fieldId}
						className="min-h-11"
						disabled
						placeholder={`Select ${dynamicSpec.depends_on.join(", ")} first...`}
					/>
					{property.description && (
						<p
							id={descriptionId}
							className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
						>
							{property.description}
						</p>
					)}
				</div>
			);
		}

		// Show loading state
		if (isLoading) {
			return (
				<div className="min-w-0 space-y-2">
					<FieldLabel
						title={property.title || fieldName}
						help={help}
					/>
					<Skeleton className="h-11 w-full" />
				</div>
			);
		}

		// Show error with manual fallback
		if (error || manualMode) {
			return (
				<div className="min-w-0 space-y-2">
					<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
						<FieldLabel
							htmlFor={fieldId}
							title={property.title || fieldName}
							isRequired={isRequired}
							help={help}
						/>
						{manualMode && (
							<Button
								type="button"
								variant="ghost"
								size="sm"
								className="min-h-11 px-3 text-sm text-muted-foreground"
								onClick={() => setManualMode(false)}
							>
								Use list
							</Button>
						)}
					</div>
					<div className="flex items-center gap-2">
						<Input
							aria-required={isRequired}
							aria-describedby={
								property.description ? descriptionId : undefined
							}
							id={fieldId}
							value={(value as string) ?? ""}
							onChange={(e) =>
								onChange(e.target.value || undefined)
							}
							placeholder={
								property.description ||
								`Enter ${property.title || fieldName}...`
							}
							className={cn(
								"min-h-11",
								error && "border-[var(--bf-warning)]",
							)}
						/>
						{error && (
							<AlertCircle className="h-4 w-4 text-[var(--bf-warning)] flex-shrink-0" />
						)}
					</div>
					{property.description && (
						<p
							id={descriptionId}
							className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
						>
							{property.description}
						</p>
					)}
					{error && (
						<div
							className="rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-3 text-sm leading-6 text-[var(--bf-warning)]"
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
											className="min-h-11 px-3 text-sm"
											onClick={() => void refetch()}
											disabled={isFetching}
										>
											<RefreshCw
												className={cn(
													"mr-1.5 h-3 w-3",
													isFetching &&
														"motion-safe:animate-spin",
												)}
											/>
											Retry
										</Button>
										{!manualMode && (
											<Button
												type="button"
												variant="ghost"
												size="sm"
												className="min-h-11 px-3 text-sm"
												onClick={() =>
													setManualMode(true)
												}
											>
												Enter manually
											</Button>
										)}
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
			<div className="min-w-0 space-y-2">
				<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
					<FieldLabel
						title={property.title || fieldName}
						isRequired={isRequired}
						help={help}
					/>
					<button
						type="button"
						className="min-h-11 rounded-[var(--bf-radius-control)] px-3 text-sm text-muted-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						onClick={() => setManualMode(true)}
					>
						Enter manually
					</button>
				</div>
				<Popover open={open} onOpenChange={setOpen}>
					<PopoverTrigger asChild>
						<Button
							variant="outline"
							type="button"
							id={fieldId}
							aria-label={`${property.title || fieldName}: ${selectedLabel || (value !== undefined && value !== "" ? String(value) : "Select an option")}`}
							aria-describedby={
								property.description ? descriptionId : undefined
							}
							role="combobox"
							aria-required={isRequired}
							aria-expanded={open}
							className="h-auto min-h-11 w-full justify-between whitespace-normal py-2 text-left font-normal"
						>
							<span
								className={cn(
									"min-w-0 [overflow-wrap:anywhere]",
									!selectedLabel && "text-muted-foreground",
								)}
							>
								{selectedLabel ||
									(value !== undefined && value !== ""
										? String(value)
										: undefined) ||
									`Select ${property.title || fieldName}...`}
							</span>
							<ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
						</Button>
					</PopoverTrigger>
					<PopoverContent variant="picker"
						aria-label={`${property.title || fieldName} options`}
						className="p-0"
						align="start"
					>
						<Command>
							<CommandInput
								aria-label={`Search ${property.title || fieldName}`}
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
												className="min-h-11"
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
												<div className="flex min-w-0 flex-col [overflow-wrap:anywhere]">
													<span>{optLabel}</span>
													{optDesc && (
														<span className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
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
					<p
						id={descriptionId}
						className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
					>
						{property.description}
					</p>
				)}
			</div>
		);
	}

	// Default: text input
	const isPassword = property.format === "password";
	const showStoredSecret = isPassword && secretConfigured;

	return (
		<div className="min-w-0 space-y-2">
			<div className="flex min-w-0 flex-wrap items-center gap-2">
				<FieldLabel
					htmlFor={fieldId}
					title={property.title || fieldName}
					isRequired={isRequired}
					help={help}
				/>
				{showStoredSecret && (
					<Badge variant="secondary" className="text-xs font-normal">
						<CheckCircle2 aria-hidden="true" className="mr-1 size-3" />
						Secret configured
					</Badge>
				)}
			</div>
			<Input
				aria-required={isRequired}
				aria-describedby={
					property.description ? descriptionId : undefined
				}
				id={fieldId}
				className="min-h-11"
				type={isPassword ? "password" : "text"}
				value={(value as string) ?? ""}
				onChange={(e) => onChange(e.target.value || undefined)}
				placeholder={
					showStoredSecret
						? "Leave blank to keep the current secret"
						: property.description ||
							`Enter ${property.title || fieldName}...`
				}
			/>
			{property.description && (
				<p
					id={descriptionId}
					className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]"
				>
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
	secretConfigured = false,
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

	const optionContext = JSON.stringify([
		adapterName,
		integrationId ?? null,
		organizationId ?? null,
	]);
	const previousOptionContext = useRef(optionContext);

	// Normalize incoming configurations whose required parent selections are absent.
	useEffect(() => {
		const newConfig = { ...config };
		let hasChanges = false;
		if (previousOptionContext.current !== optionContext) {
			previousOptionContext.current = optionContext;
			for (const field of fieldOrder) {
				if (
					properties[field]?.["x-dynamic-values"] &&
					Object.hasOwn(newConfig, field)
				) {
					delete newConfig[field];
					clearDependentValues(newConfig, properties, field);
					hasChanges = true;
				}
			}
		}
		let changed = true;
		while (changed) {
			changed = false;
			for (const field of fieldOrder) {
				if (
					Object.hasOwn(newConfig, field) &&
					properties[field]?.["x-dynamic-values"]?.depends_on?.some(
						(dep) => !hasConfigValue(newConfig[dep]),
					)
				) {
					delete newConfig[field];
					changed = true;
					hasChanges = true;
				}
			}
		}
		if (hasChanges) onChange(newConfig);
	}, [config, fieldOrder, properties, onChange, optionContext]);

	const handleFieldChange = (fieldName: string, value: unknown) => {
		const newConfig = { ...config };
		if (!hasConfigValue(value)) delete newConfig[fieldName];
		else newConfig[fieldName] = value;
		if (!Object.is(config[fieldName], value))
			clearDependentValues(newConfig, properties, fieldName);
		onChange(newConfig);
	};

	if (Object.keys(properties).length === 0) {
		return null;
	}

	return (
		<div className="min-w-0 space-y-4">
			{fieldOrder.map((fieldName) => {
				const property = properties[fieldName];
				if (!property) return null;

				return (
					<DynamicField
						key={`${optionContext}:${fieldName}`}
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
						secretConfigured={secretConfigured}
					/>
				);
			})}
		</div>
	);
}
