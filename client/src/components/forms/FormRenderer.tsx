import { useState, useEffect, useMemo, useRef, useCallback, memo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
	useForm,
	useWatch,
	type Control,
	type Resolver,
	type UseFormSetValue,
} from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { motion, AnimatePresence } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";
import { FormConfirmation } from "@/components/forms/FormConfirmation";
import { FormCaptcha } from "@/components/forms/FormCaptcha";
import { JsxTemplateRenderer } from "@/components/ui/jsx-template-renderer";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { MultiCombobox } from "@/components/forms/MultiCombobox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Loader2, Code2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import DOMPurify from "dompurify";
import ReactMarkdown from "react-markdown";
import type { components } from "@/lib/v1";
import type {
	FormField,
	FormSchema,
	DataProviderInputConfig,
} from "@/lib/client-types";
import { getEmbedTokenClaims } from "@/lib/auth-token";
import { formRuntimeQueryParams } from "@/lib/form-embed-presentation";

type Form =
	| components["schemas"]["FormPublic"]
	| components["schemas"]["FormRuntimeDefinition"];
import { useSubmitForm } from "@/hooks/useForms";

import type { DataProviderOption } from "@/services/dataProviders";
import { getFormFieldOptions } from "@/services/dataProviders";
import { FormContextProvider, useFormContext } from "@/contexts/FormContext";
import { useLaunchWorkflow } from "@/hooks/useLaunchWorkflow";
import { FormContextPanel } from "@/components/forms/FormContextPanel";
import { FileUploadField } from "@/components/forms/FileUploadField";
import {
	ScheduleControls,
	type Schedule,
} from "@/components/execution/ScheduleControls";
import { toast } from "sonner";

function createSubmissionNonce(): string {
	const browserCrypto = globalThis.crypto;
	if (typeof browserCrypto?.randomUUID === "function") {
		return browserCrypto.randomUUID();
	}
	if (typeof browserCrypto?.getRandomValues === "function") {
		const bytes = browserCrypto.getRandomValues(new Uint8Array(24));
		return Array.from(bytes, (byte) =>
			byte.toString(16).padStart(2, "0"),
		).join("");
	}
	return `form-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Memo-safe checkbox bound to react-hook-form via `useWatch`. Using
 * `useWatch` (rather than `watch(name)`) keeps the React Compiler from
 * skipping memoization for the parent component.
 */
function CheckboxField({
	field,
	control,
	setValue,
	error,
}: {
	field: FormField;
	control: Control<Record<string, unknown>>;
	setValue: UseFormSetValue<Record<string, unknown>>;
	error: ReturnType<typeof useForm>["formState"]["errors"][string];
}) {
	const value = useWatch({ control, name: field.name });
	return (
		<div className="space-y-2">
			<div className="flex items-center space-x-2">
				<Checkbox
					id={field.name}
					checked={value === true}
					onCheckedChange={(checked) =>
						setValue(field.name, checked, {
							shouldValidate: true,
						})
					}
				/>
				<Label htmlFor={field.name} className="cursor-pointer">
					{field.label}
					{field.required && (
						<span className="text-destructive ml-1">*</span>
					)}
				</Label>
			</div>
			{field.help_text && (
				<p className="text-sm text-muted-foreground">
					{field.help_text}
				</p>
			)}
			{error && (
				<p className="text-sm text-destructive">
					{error.message as string}
				</p>
			)}
		</div>
	);
}

interface FormRendererProps {
	form: Form;
	/** Show developer context panel (platform admin only) */
	devMode?: boolean;
	/** Callback to toggle dev mode (only provided for platform admins) */
	onDevModeChange?: (enabled: boolean) => void;
	/** Callback when execution starts (called with execution ID) */
	onExecutionStart?: (executionId: string) => void;
	/** If true, don't navigate after submission (for embedded forms) */
	preventNavigation?: boolean;
	/** Whether deferred-execution controls are available. */
	allowScheduling?: boolean;
}

// Helper function to convert DataProviderOption[] to ComboboxOption[]
function toComboboxOptions(
	options: DataProviderOption[] | undefined,
): ComboboxOption[] {
	if (!options) return [];
	return options.map((opt) => ({
		value: opt.value,
		label: opt.label,
		...(opt.description ? { description: opt.description } : {}),
	}));
}

// Type guard to check if form_schema is a valid FormSchema
function isFormSchema(schema: unknown): schema is FormSchema {
	return (
		schema !== null &&
		typeof schema === "object" &&
		"fields" in schema &&
		Array.isArray((schema as FormSchema).fields)
	);
}

function hasDynamicOptions(field: FormField): boolean {
	return (
		field.has_dynamic_options === true || Boolean(field.data_provider_id)
	);
}

function providerCacheKey(field: FormField): string {
	return field.name;
}

/**
 * Inner component that uses FormContext
 * Separated to allow FormContextProvider to wrap it
 */
function FormRendererInner({
	form,
	devMode,
	onDevModeChange,
	onExecutionStart,
	preventNavigation,
	allowScheduling = !preventNavigation,
}: FormRendererProps) {
	const navigate = useNavigate();
	const submitForm = useSubmitForm();
	const {
		context,
		startupHandle,
		isFieldVisible,
		setFieldValue,
		isLoadingLaunchWorkflow,
	} = useFormContext();

	// Execute launch workflow if configured
	useLaunchWorkflow({ form });

	// Get typed fields array - memoized to prevent unnecessary re-renders
	const fields = useMemo(
		() => (isFormSchema(form.form_schema) ? form.form_schema.fields : []),
		[form.form_schema],
	);

	// Single state object for all data provider state to batch updates
	const [dataProviderState, setDataProviderState] = useState<{
		options: Record<string, DataProviderOption[]>;
		loading: Record<string, boolean>;
		errors: Record<string, string>;
		successfullyLoaded: Set<string>;
	}>({
		options: {},
		loading: {},
		errors: {},
		successfullyLoaded: new Set(),
	});

	// Track which inputs we've attempted to load (to prevent infinite loops)
	const loadedInputsRef = useRef<Record<string, string>>({});

	// Track blur events to trigger data provider loading
	const fieldBlurTriggerRef = useRef<number>(0);

	// Track if initial load is complete
	const [hasCompletedInitialLoad, setHasCompletedInitialLoad] =
		useState(false);

	// Track navigation state to keep button disabled through redirect
	const [isNavigating, setIsNavigating] = useState(false);
	const [confirmationMarkdown, setConfirmationMarkdown] = useState<
		string | null
	>(null);
	const [honeypot, setHoneypot] = useState("");
	const [captchaPayload, setCaptchaPayload] = useState<string | null>(null);
	const [captchaResetSignal, setCaptchaResetSignal] = useState(0);
	const captchaRequired =
		"captcha_required" in form && form.captcha_required === true;

	// Track which file fields are currently uploading
	const [uploadingFields, setUploadingFields] = useState<Set<string>>(
		new Set(),
	);

	// Deferred execution: null = run now, non-null = scheduled.
	const [schedule, setSchedule] = useState<Schedule | null>(null);

	// Ref for setValue so loadDataProviders can call it for auto_fill
	// (loadDataProviders is defined before useForm, so we use a ref bridge)
	const setValueRef = useRef<
		| ((
				name: string,
				value: unknown,
				options?: { shouldValidate?: boolean },
		  ) => void)
		| null
	>(null);

	// Helper to evaluate data provider inputs (T040, T055, T075 - All three modes)
	// Accepts optional fieldOverrides to use fresh values before context has updated
	const evaluateDataProviderInputs = useCallback(
		(
			field: FormField,
			fieldOverrides?: Record<string, unknown>,
		): {
			inputs: Record<string, unknown> | null;
			hasAllRequired: boolean;
		} => {
			if (!field.data_provider_inputs)
				return { inputs: null, hasAllRequired: true };

			const inputValues: Record<string, unknown> = {};
			let hasAllRequired = true;

			// Merge context.field with any overrides (overrides take precedence)
			const fieldValues = fieldOverrides
				? { ...context.field, ...fieldOverrides }
				: context.field;

			Object.entries(field.data_provider_inputs).forEach(
				([key, config]: [string, DataProviderInputConfig]) => {
					switch (config.mode) {
						case "static":
							if (
								config.value !== null &&
								config.value !== undefined &&
								config.value !== ""
							) {
								inputValues[key] = config.value;
							}
							break;

						case "fieldRef":
							if (
								config.field_name &&
								fieldValues[config.field_name] !== undefined &&
								fieldValues[config.field_name] !== ""
							) {
								inputValues[key] =
									fieldValues[config.field_name];
							} else {
								hasAllRequired = false;
							}
							break;

						case "expression":
							if (config.expression) {
								try {
									// Simple expression evaluation using Function constructor
									// context.field.fieldName or context.workflow.property
									// Use a merged context with the field overrides
									const evalContext = fieldOverrides
										? {
												...context,
												field: fieldValues,
											}
										: context;
									const evalFunc = new Function(
										"context",
										`return ${config.expression}`,
									);
									const result = evalFunc(evalContext);
									if (result && result !== "") {
										inputValues[key] = result;
									} else {
										hasAllRequired = false;
									}
								} catch {
									hasAllRequired = false;
								}
							}
							break;
					}
				},
			);

			return {
				inputs:
					Object.keys(inputValues).length > 0 ? inputValues : null,
				hasAllRequired,
			};
		},
		[context],
	);

	// Function to load data providers (called on mount and blur events)
	// Accepts optional fieldOverrides to use fresh values before context has updated
	const loadDataProviders = useCallback(
		async (fieldOverrides?: Record<string, unknown>) => {
			const selectFields = fields.filter(hasDynamicOptions);

			for (const field of selectFields) {
				const cacheKey = providerCacheKey(field);

				// Evaluate inputs to check if all required fields are available
				// Pass fieldOverrides to use fresh values
				const { inputs, hasAllRequired } = evaluateDataProviderInputs(
					field,
					fieldOverrides,
				);

				// Skip if we don't have all required inputs
				if (!hasAllRequired) {
					// Clear any existing options and errors
					setDataProviderState((prev) => ({
						...prev,
						options: {
							...prev.options,
							[cacheKey]: undefined,
						} as Record<string, DataProviderOption[]>,
						errors: {
							...prev.errors,
							[cacheKey]: undefined,
						} as Record<string, string>,
					}));
					continue;
				}

				// Create a hash of the inputs to detect changes
				const inputsHash = JSON.stringify(inputs || {});

				// Skip if we've already loaded with these exact inputs
				if (loadedInputsRef.current[cacheKey] === inputsHash) {
					continue;
				}

				// Skip if already loading
				if (dataProviderState.loading[cacheKey]) {
					continue;
				}

				try {
					// Set loading state
					setDataProviderState((prev) => ({
						...prev,
						loading: { ...prev.loading, [cacheKey]: true },
						errors: {
							...prev.errors,
							[cacheKey]: undefined,
						} as Record<string, string>,
					}));

					const options = await getFormFieldOptions(
						form.id,
						field.name,
						inputs || undefined,
					);

					loadedInputsRef.current[cacheKey] = inputsHash;

					// Update all state in one batch
					setDataProviderState((prev) => {
						const newSuccessfullyLoaded = new Set(
							prev.successfullyLoaded,
						);
						newSuccessfullyLoaded.add(cacheKey);
						return {
							options: { ...prev.options, [cacheKey]: options },
							loading: { ...prev.loading, [cacheKey]: false },
							errors: prev.errors,
							successfullyLoaded: newSuccessfullyLoaded,
						};
					});

					// Auto-fill sibling fields from data provider metadata
					if (field.auto_fill && options.length > 0) {
						const metadata = options[0].metadata;
						if (metadata && setValueRef.current) {
							Object.entries(field.auto_fill).forEach(
								([targetField, metadataKey]) => {
									const value = metadata[metadataKey];
									if (value !== undefined && value !== null) {
										setValueRef.current?.(
											targetField,
											value,
											{
												shouldValidate: true,
											},
										);
									}
								},
							);
						}
					}
				} catch {
					// Update error state in one batch
					setDataProviderState((prev) => ({
						...prev,
						loading: { ...prev.loading, [cacheKey]: false },
						errors: {
							...prev.errors,
							[cacheKey]: "Unable to load data",
						},
						options: {
							...prev.options,
							[cacheKey]: undefined,
						} as Record<string, DataProviderOption[]>,
					}));
				}
			}
		},
		// eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omit dataProviderState.loading to prevent infinite loop
		[fields, evaluateDataProviderInputs, form.id],
	);

	// Load data providers on mount and when fieldBlurTrigger changes
	useEffect(() => {
		loadDataProviders().then(() => {
			// Mark initial load as complete after first load
			if (!hasCompletedInitialLoad) {
				setHasCompletedInitialLoad(true);
			}
		});
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [fields, fieldBlurTriggerRef.current]);

	// Handler for field blur events
	const handleFieldBlur = useCallback(() => {
		// Increment trigger to cause useEffect to re-run
		fieldBlurTriggerRef.current += 1;
		loadDataProviders();
	}, [loadDataProviders]);

	// Build Zod schema dynamically from form fields
	// Note: This function is called on every render to ensure the latest dataProviderState is used
	// The schema is only evaluated during form submission, so this doesn't cause performance issues
	const buildSchema = useCallback(() => {
		const schemaFields: Record<string, z.ZodTypeAny> = {};

		fields.forEach((field: FormField) => {
			let fieldSchema: z.ZodTypeAny;

			switch (field.type) {
				case "email":
					fieldSchema = z.string().email("Invalid email address");
					break;
				case "number":
					fieldSchema = z.coerce.number();
					break;
				case "checkbox":
					fieldSchema = z.boolean();
					break;
				case "multi_select":
					fieldSchema = z.array(z.string());
					break;
				case "file":
					// File fields store S3 paths as strings (or array of strings for multiple)
					if (field.multiple) {
						fieldSchema = z.array(z.string()).nullable();
					} else {
						fieldSchema = z.string().nullable();
					}
					break;
				default:
					fieldSchema = z.string();
			}

			// Apply required validation
			if (field.required) {
				if (field.type === "checkbox") {
					fieldSchema = z.boolean().refine((val) => val === true, {
						message: "This field is required",
					});
				} else if (field.type === "multi_select") {
					fieldSchema = z
						.array(z.string())
						.min(1, "Select at least one option");
				} else if (field.type === "file") {
					// File fields: check for non-null and non-empty
					if (field.multiple) {
						fieldSchema = z
							.array(z.string())
							.nullable()
							.refine((val) => val !== null && val.length > 0, {
								message: "At least one file is required",
							});
					} else {
						fieldSchema = z
							.string()
							.nullable()
							.refine((val) => val !== null && val !== "", {
								message: "This field is required",
							});
					}
				} else {
					fieldSchema = fieldSchema.refine((val) => val !== "", {
						message: "This field is required",
					});
				}
			} else {
				fieldSchema = fieldSchema.optional();
			}

			// Add validation for data provider fields with required inputs
			if (hasDynamicOptions(field) && field.data_provider_inputs) {
				const cacheKey = providerCacheKey(field);

				// Check if any inputs are required
				const hasRequiredInputs = Object.values(
					field.data_provider_inputs,
				).some((inputConfig: DataProviderInputConfig) => {
					// An input is considered "required" if it has a configuration
					// (all configured inputs must be satisfied for the provider to load)
					return (
						inputConfig.mode === "static" ||
						inputConfig.mode === "fieldRef" ||
						inputConfig.mode === "expression"
					);
				});

				if (hasRequiredInputs) {
					// Add refinement to check if data provider has successfully loaded
					fieldSchema = fieldSchema.superRefine((_val, ctx) => {
						if (
							!dataProviderState.successfullyLoaded.has(cacheKey)
						) {
							ctx.addIssue({
								code: z.ZodIssueCode.custom,
								message:
									"Data must be loaded before submitting. Please complete all required input fields.",
							});
						}
					});
				}
			}

			schemaFields[field.name] = fieldSchema;
		});

		return z.object(schemaFields);
	}, [fields, dataProviderState.successfullyLoaded]);

	// Create a custom resolver that calls buildSchema() to get the latest schema
	const customResolver = useCallback<Resolver<Record<string, unknown>>>(
		async (data, context, options) => {
			const schema = buildSchema();
			const resolver = zodResolver(schema);
			return resolver(data, context, options);
		},
		[buildSchema],
	);

	const {
		register,
		handleSubmit,
		formState: { errors, isValid },
		setValue,
		watch,
		control,
	} = useForm({
		resolver: customResolver,
		mode: "onChange", // Validate on change to keep isValid up-to-date
		defaultValues: fields.reduce(
			(acc: Record<string, unknown>, field: FormField) => {
				if (field.type === "multi_select") {
					// default_value is a comma-separated list of option values
					if (
						typeof field.default_value === "string" &&
						field.default_value.length > 0
					) {
						acc[field.name] = field.default_value
							.split(",")
							.map((v) => v.trim())
							.filter((v) => v.length > 0);
					} else if (Array.isArray(field.default_value)) {
						acc[field.name] = field.default_value;
					} else {
						acc[field.name] = [];
					}
				} else if (
					field.default_value !== undefined &&
					field.default_value !== null
				) {
					acc[field.name] = field.default_value;
				} else if (field.type === "checkbox") {
					acc[field.name] = false;
				} else if (field.type === "file") {
					acc[field.name] = null; // File fields start as null
				} else {
					acc[field.name] = "";
				}
				return acc;
			},
			{} as Record<string, unknown>,
		),
	});

	// Bridge setValue to the ref so loadDataProviders (defined earlier) can use it for auto_fill
	setValueRef.current = setValue;

	// Watch all field values and sync to FormContext for visibility evaluation
	// Use ref to track previous values to avoid infinite loops
	const formValues = watch();
	const prevValuesRef = useRef<Record<string, unknown>>({});

	useEffect(() => {
		// Track which fields changed
		const changedFields: string[] = [];

		// Only update fields that have actually changed
		Object.entries(formValues).forEach(([fieldName, value]) => {
			if (prevValuesRef.current[fieldName] !== value) {
				prevValuesRef.current[fieldName] = value;
				setFieldValue(fieldName, value);
				changedFields.push(fieldName);
			}
		});

		// Clear data provider data for any fields that depend on changed fields
		if (changedFields.length > 0) {
			const fieldsWithProviders = fields.filter(
				(field: FormField) =>
					hasDynamicOptions(field) && field.data_provider_inputs,
			);

			const fieldsToClear: string[] = [];

			fieldsWithProviders.forEach((field: FormField) => {
				// Check if this field depends on any of the changed fields
				Object.values(field.data_provider_inputs || {}).forEach(
					(inputConfig: DataProviderInputConfig) => {
						if (
							inputConfig.mode === "fieldRef" &&
							inputConfig.field_name
						) {
							if (
								changedFields.includes(inputConfig.field_name)
							) {
								fieldsToClear.push(providerCacheKey(field));
							}
						}
					},
				);
			});

			// Clear data for dependent fields and reload them
			if (fieldsToClear.length > 0) {
				setDataProviderState((prev) => {
					const newOptions = { ...prev.options };
					const newSuccessfullyLoaded = new Set(
						prev.successfullyLoaded,
					);

					fieldsToClear.forEach((cacheKey) => {
						delete newOptions[cacheKey];
						newSuccessfullyLoaded.delete(cacheKey);
						// Also clear from loadedInputsRef so loadDataProviders will reload
						delete loadedInputsRef.current[cacheKey];
					});

					return {
						...prev,
						options: newOptions,
						successfullyLoaded: newSuccessfullyLoaded,
					};
				});

				// Trigger reload of dependent data providers
				// Pass current formValues as overrides since context.field may not be updated yet
				loadDataProviders(formValues);
			}
		}
	}, [formValues, setFieldValue, fields, loadDataProviders]);

	const onSubmit = async (data: Record<string, unknown>) => {
		setIsNavigating(true);
		try {
			const result = await submitForm.mutateAsync({
				params: { path: { form_id: form.id } },
				body: {
					form_data: data,
					startup_handle: startupHandle,
					submission_nonce: createSubmissionNonce(),
					honeypot,
					captcha_payload: captchaPayload,
					...(schedule ?? {}),
				},
			});

			if (result.mode === "confirmation") {
				setConfirmationMarkdown(result.confirmation_markdown);
				return;
			}

			// Call callback if provided (for embedded forms)
			if (onExecutionStart) {
				onExecutionStart(result.execution_id);
			}

			// Scheduled run: the workflow hasn't started yet. Send the user to
			// /history (where the new row will show with the Scheduled badge)
			// with a toast showing the scheduled time. Embedded forms opt out
			// of this navigation via preventNavigation and surface the state
			// through onExecutionStart instead.
			if (result.status === "Scheduled") {
				if (!preventNavigation) {
					const when = result.scheduled_at
						? new Date(result.scheduled_at).toLocaleString()
						: "later";
					toast.success(`Scheduled for ${when}`);
					navigate("/history");
				}
				return;
			}

			// Only navigate if not prevented (embedded forms handle their own display)
			if (!preventNavigation) {
				navigate(`/history/${result.execution_id}`, {
					state: {
						workflow_name: form.name,
						form_id: form.id,
						input_data: data,
					},
				});
			}
			// Don't reset isNavigating - component will unmount on navigation (or stay disabled in embedded mode)
		} catch {
			if (captchaRequired) {
				setCaptchaPayload(null);
				setCaptchaResetSignal((current) => current + 1);
			}
			setIsNavigating(false); // Only re-enable button on error
		}
	};

	// Props interface for DataProviderField
	interface DataProviderFieldProps {
		fieldName: string;
		fieldLabel: string | null;
		fieldRequired: boolean;
		fieldPlaceholder: string | null;
		fieldHelpText: string | null;
		options: ComboboxOption[];
		isLoading: boolean;
		error: { message?: string } | undefined;
		providerError: string | undefined;
		isEnabled: boolean;
		value: string;
		onValueChange: (value: string) => void;
	}

	// Memoized data provider field renderer - only re-renders when its specific data changes
	const DataProviderField = memo(
		({
			fieldName,
			fieldLabel,
			fieldRequired,
			fieldPlaceholder,
			fieldHelpText,
			options,
			isLoading,
			error: fieldError,
			providerError,
			isEnabled,
			value,
			onValueChange,
		}: DataProviderFieldProps) => (
			<div className="space-y-2">
				<Label htmlFor={fieldName}>
					{fieldLabel}
					{fieldRequired && (
						<span className="text-destructive ml-1">*</span>
					)}
				</Label>
				<Combobox
					id={fieldName}
					options={options && options.length > 0 ? options : []}
					value={value}
					onValueChange={onValueChange}
					placeholder={fieldPlaceholder || "Select an option..."}
					emptyText="No options available"
					isLoading={isLoading}
					disabled={!isEnabled || isLoading}
				/>
				{providerError && (
					<p className="text-sm text-destructive">{providerError}</p>
				)}
				{!providerError && fieldHelpText && (
					<p className="text-sm text-muted-foreground">
						{fieldHelpText}
					</p>
				)}
				{fieldError && (
					<p className="text-sm text-destructive">
						{fieldError.message as string}
					</p>
				)}
			</div>
		),
		(prevProps, nextProps) => {
			// Only re-render if these specific props change
			return (
				prevProps.fieldName === nextProps.fieldName &&
				prevProps.fieldLabel === nextProps.fieldLabel &&
				prevProps.fieldRequired === nextProps.fieldRequired &&
				prevProps.fieldPlaceholder === nextProps.fieldPlaceholder &&
				prevProps.fieldHelpText === nextProps.fieldHelpText &&
				prevProps.options === nextProps.options &&
				prevProps.isLoading === nextProps.isLoading &&
				prevProps.providerError === nextProps.providerError &&
				prevProps.isEnabled === nextProps.isEnabled &&
				prevProps.value === nextProps.value &&
				prevProps.error === nextProps.error &&
				prevProps.onValueChange === nextProps.onValueChange
			);
		},
	);

	DataProviderField.displayName = "DataProviderField";

	// Create stable callbacks for field value changes
	const fieldValueChangeCallbacks = useRef<
		Record<string, (value: string) => void>
	>({});

	// Helper: apply auto_fill from a selected option's metadata to sibling fields
	const applyAutoFill = useCallback(
		(field: FormField, selectedValue: string) => {
			if (!field.auto_fill || !hasDynamicOptions(field)) return;
			const cacheKey = providerCacheKey(field);
			const options = dataProviderState.options[cacheKey];
			if (!options) return;

			const selectedOption = options.find(
				(opt) => opt.value === selectedValue,
			);
			const metadata = selectedOption?.metadata;
			if (!metadata) return;

			Object.entries(field.auto_fill).forEach(
				([targetField, metadataKey]) => {
					const value = metadata[metadataKey];
					if (value !== undefined && value !== null) {
						setValue(targetField, value, {
							shouldValidate: true,
						});
					}
				},
			);
		},
		[dataProviderState.options, setValue],
	);

	const getFieldValueChangeCallback = useCallback(
		(fieldName: string, field?: FormField) => {
			// Invalidate cached callback when auto_fill options change so
			// the closure always references the latest options array.
			const cacheKey =
				field && hasDynamicOptions(field) ? fieldName : undefined;
			const currentOptions = cacheKey
				? dataProviderState.options[cacheKey]
				: undefined;
			const cacheId = `${fieldName}_${field?.auto_fill ? "af" : ""}${currentOptions ? currentOptions.length : 0}`;

			if (
				!fieldValueChangeCallbacks.current[fieldName] ||
				(fieldValueChangeCallbacks.current as Record<string, unknown>)[
					`${fieldName}_cacheId`
				] !== cacheId
			) {
				fieldValueChangeCallbacks.current[fieldName] = (
					value: string,
				) => {
					setValue(fieldName, value, { shouldValidate: true });
					if (field?.auto_fill) {
						applyAutoFill(field, value);
					}
				};
				(fieldValueChangeCallbacks.current as Record<string, unknown>)[
					`${fieldName}_cacheId`
				] = cacheId;
			}
			return fieldValueChangeCallbacks.current[fieldName];
		},
		[setValue, applyAutoFill, dataProviderState.options],
	);

	const renderField = (field: FormField) => {
		const error = errors[field.name];

		// If field has a data provider, render it as a single-select dropdown regardless
		// of type — EXCEPT for multi_select, which has its own render case below that
		// uses the same provider-loaded options but with a multi-select UI.
		if (hasDynamicOptions(field) && field.type !== "multi_select") {
			const cacheKey = providerCacheKey(field);
			const options = cacheKey ? dataProviderState.options[cacheKey] : [];
			const isLoadingOptions = cacheKey
				? dataProviderState.loading[cacheKey]
				: false;
			const providerError = cacheKey
				? dataProviderState.errors[cacheKey]
				: undefined;
			const hasSuccessfullyLoaded = cacheKey
				? dataProviderState.successfullyLoaded.has(cacheKey)
				: false;

			return (
				<DataProviderField
					fieldName={field.name}
					fieldLabel={field.label ?? null}
					fieldRequired={field.required ?? false}
					fieldPlaceholder={field.placeholder ?? null}
					fieldHelpText={field.help_text ?? null}
					options={toComboboxOptions(options)}
					isLoading={!!isLoadingOptions}
					error={error}
					providerError={providerError || undefined}
					isEnabled={hasSuccessfullyLoaded}
					value={(formValues[field.name] as string) || ""}
					onValueChange={getFieldValueChangeCallback(
						field.name,
						field,
					)}
				/>
			);
		}

		switch (field.type) {
			case "textarea":
				return (
					<div className="space-y-2">
						<Label htmlFor={field.name}>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Textarea
							id={field.name}
							placeholder={field.placeholder ?? undefined}
							{...register(field.name)}
							onBlur={handleFieldBlur}
						/>
						{field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);

			case "checkbox":
				return (
					<CheckboxField
						field={field}
						control={control}
						setValue={setValue}
						error={error}
					/>
				);

			case "select": {
				const dynamic = hasDynamicOptions(field);
				const cacheKey = dynamic ? providerCacheKey(field) : undefined;
				const staticOptions = (field.options || []) as Array<{
					label: string;
					value: string;
				}>;
				const dynamicOptions = cacheKey
					? dataProviderState.options[cacheKey]
					: [];
				const options = dynamic ? dynamicOptions : staticOptions;
				const isLoadingOptions = cacheKey
					? dataProviderState.loading[cacheKey]
					: false;
				const providerError = cacheKey
					? dataProviderState.errors[cacheKey]
					: undefined;
				const hasSuccessfullyLoaded = cacheKey
					? dataProviderState.successfullyLoaded.has(cacheKey)
					: true; // Static options are always "loaded"

				return (
					<div className="space-y-2">
						<Label htmlFor={field.name}>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Combobox
							id={field.name}
							options={
								options && options.length > 0 ? options : []
							}
							value={formValues[field.name] as string}
							onValueChange={(value) =>
								setValue(field.name, value, {
									shouldValidate: true,
								})
							}
							placeholder={
								field.placeholder || "Select an option..."
							}
							emptyText="No options available"
							isLoading={!!isLoadingOptions}
							disabled={
								!!isLoadingOptions ||
								(dynamic && !hasSuccessfullyLoaded)
							}
						/>
						{providerError && (
							<p className="text-sm text-destructive">
								{providerError}
							</p>
						)}
						{!providerError && field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);
			}

			case "multi_select": {
				const dynamic = hasDynamicOptions(field);
				const cacheKey = dynamic ? providerCacheKey(field) : undefined;
				const staticOptions = (field.options || []) as Array<{
					label: string;
					value: string;
				}>;
				const dynamicOptions = cacheKey
					? toComboboxOptions(dataProviderState.options[cacheKey])
					: [];
				const options = dynamic ? dynamicOptions : staticOptions;
				const isLoadingOptions = cacheKey
					? dataProviderState.loading[cacheKey]
					: false;
				const providerError = cacheKey
					? dataProviderState.errors[cacheKey]
					: undefined;
				const hasSuccessfullyLoaded = cacheKey
					? dataProviderState.successfullyLoaded.has(cacheKey)
					: true;
				const currentValue = Array.isArray(formValues[field.name])
					? (formValues[field.name] as string[])
					: [];

				return (
					<div className="space-y-2">
						<Label htmlFor={field.name}>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<MultiCombobox
							id={field.name}
							options={
								options && options.length > 0 ? options : []
							}
							value={currentValue}
							onValueChange={(next) =>
								setValue(field.name, next, {
									shouldValidate: true,
								})
							}
							placeholder={
								field.placeholder || "Select options..."
							}
							emptyText="No options available"
							isLoading={!!isLoadingOptions}
							disabled={
								!!isLoadingOptions ||
								(dynamic && !hasSuccessfullyLoaded)
							}
						/>
						{providerError && (
							<p className="text-sm text-destructive">
								{providerError}
							</p>
						)}
						{!providerError && field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);
			}

			case "radio": {
				const radioOptions = (field.options || []) as Array<{
					label: string;
					value: string;
				}>;
				const defaultVal = field.default_value as
					string | null | undefined;
				return (
					<div className="space-y-2">
						<Label>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<RadioGroup
							onValueChange={(value: string) =>
								setValue(field.name, value, {
									shouldValidate: true,
								})
							}
							{...(defaultVal
								? { defaultValue: defaultVal }
								: {})}
						>
							{radioOptions.map((option) => (
								<div
									key={option["value"]}
									className="flex items-center space-x-2"
								>
									<RadioGroupItem
										value={option["value"]}
										id={`${field.name}-${option["value"]}`}
									/>
									<Label
										htmlFor={`${field.name}-${option["value"]}`}
										className="cursor-pointer font-normal"
									>
										{option["label"]}
									</Label>
								</div>
							))}
						</RadioGroup>
						{field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);
			}

			case "datetime":
				return (
					<div className="space-y-2">
						<Label htmlFor={field.name}>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Input
							id={field.name}
							type="datetime-local"
							placeholder={field.placeholder ?? undefined}
							{...register(field.name)}
						/>
						{field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);

			case "markdown":
				return (
					<div className="space-y-2">
						<div className="prose prose-sm max-w-none dark:prose-invert">
							{field.content ? (
								<ReactMarkdown
									components={{
										// Ensure headings render properly
										h1: ({ ...props }) => (
											<h1
												className="text-2xl font-bold mt-4 mb-2"
												{...props}
											/>
										),
										h2: ({ ...props }) => (
											<h2
												className="text-xl font-bold mt-3 mb-2"
												{...props}
											/>
										),
										h3: ({ ...props }) => (
											<h3
												className="text-lg font-bold mt-2 mb-1"
												{...props}
											/>
										),
										h4: ({ ...props }) => (
											<h4
												className="text-base font-bold mt-2 mb-1"
												{...props}
											/>
										),
										h5: ({ ...props }) => (
											<h5
												className="text-sm font-bold mt-1 mb-1"
												{...props}
											/>
										),
										h6: ({ ...props }) => (
											<h6
												className="text-xs font-bold mt-1 mb-1"
												{...props}
											/>
										),
									}}
								>
									{field.content}
								</ReactMarkdown>
							) : (
								<span className="text-muted-foreground italic">
									No content provided
								</span>
							)}
						</div>
						{field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
					</div>
				);

			case "html": {
				// Support both JSX templates and static HTML
				// HTML fields are display-only components and should not show labels
				const content =
					field.content ||
					'<p className="text-muted-foreground italic">No content provided</p>';

				// Check if content looks like JSX (contains React-style attributes or JSX expressions)
				const isJsxTemplate =
					content.includes("className=") ||
					content.includes("{context.");

				if (isJsxTemplate) {
					// Render as JSX template with full context access
					return (
						<div className="space-y-2">
							<JsxTemplateRenderer
								template={content}
								context={context}
							/>
							{field.help_text && (
								<p className="text-sm text-muted-foreground">
									{field.help_text}
								</p>
							)}
						</div>
					);
				} else {
					// Fallback to sanitized HTML for backwards compatibility
					const sanitizedHtml = DOMPurify.sanitize(content);
					return (
						<div className="space-y-2">
							<div
								className="rounded-md bg-muted/50 p-4 ring-1 ring-foreground/5"
								dangerouslySetInnerHTML={{
									__html: sanitizedHtml,
								}}
							/>
							{field.help_text && (
								<p className="text-sm text-muted-foreground">
									{field.help_text}
								</p>
							)}
						</div>
					);
				}
			}

			case "file":
				return (
					<FileUploadField
						formId={form.id}
						fieldName={field.name}
						label={field.label ?? null}
						required={field.required ?? false}
						helpText={field.help_text ?? null}
						allowedTypes={field.allowed_types ?? null}
						multiple={field.multiple ?? null}
						maxSizeMb={field.max_size_mb ?? null}
						value={
							formValues[field.name] as string | string[] | null
						}
						onChange={(value) =>
							setValue(field.name, value, {
								shouldValidate: true,
							})
						}
						onUploadStart={() =>
							setUploadingFields((prev) => {
								const next = new Set(prev);
								next.add(field.name);
								return next;
							})
						}
						onUploadEnd={() =>
							setUploadingFields((prev) => {
								const next = new Set(prev);
								next.delete(field.name);
								return next;
							})
						}
						error={error}
					/>
				);

			default:
				return (
					<div className="space-y-2">
						<Label htmlFor={field.name}>
							{field.label}
							{field.required && (
								<span className="text-destructive ml-1">*</span>
							)}
						</Label>
						<Input
							id={field.name}
							type={
								field.type === "email"
									? "email"
									: field.type === "number"
										? "number"
										: "text"
							}
							placeholder={field.placeholder ?? undefined}
							{...register(field.name)}
							onBlur={handleFieldBlur}
						/>
						{field.help_text && (
							<p className="text-sm text-muted-foreground">
								{field.help_text}
							</p>
						)}
						{error && (
							<p className="text-sm text-destructive">
								{error.message as string}
							</p>
						)}
					</div>
				);
		}
	};

	// Filter fields by visibility
	// Context changes trigger re-evaluation through isFieldVisible
	const visibleFields = useMemo(() => {
		return fields.filter(isFieldVisible);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [fields, context]);

	// Show loading state while launch workflow executes or data providers load (only on initial load)
	const isAnyDataProviderLoading = Object.values(
		dataProviderState.loading,
	).some((loading) => loading);
	const showLoadingState =
		isLoadingLaunchWorkflow ||
		(!hasCompletedInitialLoad && isAnyDataProviderLoading);

	// Dev toggle component - positioned absolutely so it doesn't affect layout
	const devToggle = onDevModeChange && (
		<div className="absolute top-4 right-4 flex items-center gap-2">
			<Switch
				id="dev-mode"
				checked={devMode}
				onCheckedChange={onDevModeChange}
			/>
			<Label
				htmlFor="dev-mode"
				className="flex items-center gap-1.5 text-sm cursor-pointer"
			>
				<Code2 className="h-3.5 w-3.5" />
				Dev
			</Label>
		</div>
	);

	if (confirmationMarkdown) {
		return (
			<FormConfirmation
				formId={form.id}
				markdown={confirmationMarkdown}
			/>
		);
	}

	if (showLoadingState) {
		return (
			<div className="flex justify-center">
				<Card
					className={
						devMode
							? "w-full max-w-[calc(42rem+320px)] relative"
							: "w-full max-w-2xl relative"
					}
				>
					{devToggle}
					<div className="flex">
						{/* Form content */}
						<CardContent className="pt-6 flex-1 min-w-0 max-w-2xl">
							<div className="space-y-6">
								{/* Loading indicator */}
								<div className="flex items-center gap-3 rounded-lg bg-muted/50 p-4 ring-1 ring-foreground/5">
									<Loader2 className="h-5 w-5 animate-spin text-primary" />
									<div className="flex-1">
										<p className="text-sm font-medium">
											{isLoadingLaunchWorkflow
												? "Loading form data..."
												: "Loading form options..."}
										</p>
										<p className="text-xs text-muted-foreground mt-0.5">
											{isLoadingLaunchWorkflow
												? "Executing launch workflow to populate form context"
												: "Fetching dynamic options from data providers"}
										</p>
									</div>
								</div>

								{/* Skeleton loader for form fields */}
								<div className="space-y-4">
									<Skeleton className="h-12 w-full" />
									<Skeleton className="h-12 w-full" />
									<Skeleton className="h-24 w-full" />
									<Skeleton className="h-12 w-full" />
									<Skeleton className="h-10 w-32" />
								</div>
							</div>
						</CardContent>

						{/* Dev mode drawer */}
						<motion.div
							initial={false}
							animate={{
								width: devMode ? 320 : 0,
								opacity: devMode ? 1 : 0,
							}}
							transition={{ duration: 0.2, ease: "easeInOut" }}
							className="overflow-hidden shrink-0"
						>
							<div className="w-80 h-full border-l p-4">
								<FormContextPanel />
							</div>
						</motion.div>
					</div>
				</Card>
			</div>
		);
	}

	return (
		<div className="flex justify-center">
			<Card
				className={
					devMode
						? "w-full max-w-[calc(42rem+320px)] relative"
						: "w-full max-w-2xl relative"
				}
			>
				{devToggle}
				<div className="flex">
					{/* Form content */}
					<CardContent className="pt-6 flex-1 min-w-0 max-w-2xl">
						<form
							onSubmit={handleSubmit(onSubmit)}
							className="space-y-4"
						>
							<AnimatePresence mode="popLayout" initial={false}>
								{visibleFields.map((field: FormField) => (
									<motion.div
										key={field.name}
										initial={{
											opacity: 0,
											height: 0,
											marginBottom: 0,
										}}
										animate={{
											opacity: 1,
											height: "auto",
											marginBottom: 16,
										}}
										exit={{
											opacity: 0,
											height: 0,
											marginBottom: 0,
										}}
										transition={{
											opacity: { duration: 0.15 },
											height: {
												duration: 0.2,
												ease: "easeInOut",
											},
											marginBottom: {
												duration: 0.2,
												ease: "easeInOut",
											},
										}}
										style={{ overflow: "hidden" }}
									>
										{renderField(field)}
									</motion.div>
								))}
							</AnimatePresence>
							<input
								type="text"
								name="website"
								value={honeypot}
								onChange={(event) =>
									setHoneypot(event.target.value)
								}
								tabIndex={-1}
								autoComplete="off"
								aria-hidden="true"
								className="absolute -left-[10000px] h-px w-px overflow-hidden"
							/>
							{allowScheduling ? (
								<div className="pt-4">
									<ScheduleControls
										value={schedule}
										onChange={setSchedule}
										disabled={
											submitForm.isPending ||
											isNavigating ||
											uploadingFields.size > 0
										}
									/>
								</div>
							) : null}
							{captchaRequired ? (
								<FormCaptcha
									key={`${form.id}:${captchaResetSignal}`}
									formId={form.id}
									onPayloadChange={setCaptchaPayload}
								/>
							) : null}
							<div className="pt-4">
								<Button
									type="submit"
									disabled={
										!isValid ||
										(captchaRequired && !captchaPayload) ||
										submitForm.isPending ||
										isNavigating ||
										uploadingFields.size > 0
									}
								>
									{uploadingFields.size > 0
										? "Uploading files..."
										: submitForm.isPending || isNavigating
											? "Submitting..."
											: "Submit"}
								</Button>
							</div>
						</form>
					</CardContent>

					{/* Dev mode drawer */}
					<motion.div
						initial={false}
						animate={{
							width: devMode ? 320 : 0,
							opacity: devMode ? 1 : 0,
						}}
						transition={{ duration: 0.2, ease: "easeInOut" }}
						className="overflow-hidden shrink-0"
					>
						<div className="w-80 h-full border-l p-4">
							<FormContextPanel />
						</div>
					</motion.div>
				</div>
			</Card>
		</div>
	);
}

/**
 * FormRenderer with FormContext wrapper
 * Extracts query parameters from URL and provides them to context
 */
export function FormRenderer({
	form,
	devMode,
	onDevModeChange,
	onExecutionStart,
	preventNavigation,
	allowScheduling,
}: FormRendererProps) {
	const [searchParams] = useSearchParams();
	const isEmbed = getEmbedTokenClaims()?.embed === true;

	// Convert URLSearchParams to plain object
	const queryParams = useMemo(
		() => formRuntimeQueryParams(searchParams, isEmbed),
		[isEmbed, searchParams],
	);

	return (
		<FormContextProvider form={form} queryParams={queryParams}>
			<FormRendererInner
				form={form}
				devMode={devMode}
				onDevModeChange={onDevModeChange}
				onExecutionStart={onExecutionStart}
				preventNavigation={preventNavigation}
				allowScheduling={allowScheduling}
			/>
		</FormContextProvider>
	);
}
