/**
 * Form Context Provider
 *
 * Manages form state including:
 * - Workflow execution results (from launch workflow)
 * - Query parameters from URL
 * - Current form field values
 * - Field visibility evaluation
 *
 * All context evaluation happens client-side for instant reactivity (<100ms).
 */

import {
	createContext,
	useContext,
	useState,
	useCallback,
	useMemo,
	ReactNode,
} from "react";
import type { components } from "@/lib/v1";
import type { FormField } from "@/lib/client-types";

type Form =
	| components["schemas"]["FormPublic"]
	| components["schemas"]["FormRuntimeDefinition"];

/**
 * Form context shape matching backend spec
 * Used in visibility expressions like: context.workflow.user_exists === true
 */
export interface FormContextValue {
	/** Results from launch workflow execution */
	workflow: Record<string, unknown>;
	/** Query parameters from URL (filtered by allowed_query_params) */
	query: Record<string, string>;
	/** Current form field values */
	field: Record<string, unknown>;
}

interface FormContextProviderValue {
	/** Current form context */
	context: FormContextValue;
	/** Update workflow results (called after launch workflow executes) */
	setWorkflowResults: (results: Record<string, unknown>) => void;
	startupHandle: string | null;
	setStartupHandle: (handle: string | null) => void;
	/** Update field value (called on user input) */
	setFieldValue: (fieldName: string, value: unknown) => void;
	/** Check if a field should be visible based on its visibility_expression */
	isFieldVisible: (field: FormField) => boolean;
	/** Check if form is loading launch workflow */
	isLoadingLaunchWorkflow: boolean;
	/** Set launch workflow loading state */
	setIsLoadingLaunchWorkflow: (loading: boolean) => void;
}

const FormContext = createContext<FormContextProviderValue | undefined>(
	undefined,
);

interface FormContextProviderProps {
	children: ReactNode;
	form: Form;
	/** Initial query parameters from URL search params */
	queryParams?: Record<string, string>;
}

/**
 * Track logged errors to prevent console flooding
 */
const loggedExpressionErrors = new Set<string>();

/**
 * Safely evaluate a visibility expression using native JavaScript
 * Returns true if field should be visible, false otherwise
 *
 * SECURITY NOTE: Using Function constructor is intentional here.
 * - Only form builders (admins) write expressions, not end users
 * - Expressions execute client-side with restricted context only
 * - No access to window, document, or other sensitive objects
 */
function evaluateVisibilityExpression(
	expression: string,
	context: FormContextValue,
): boolean {
	try {
		// Create a function with context in scope
		// This allows natural JavaScript syntax: context.field.name !== null && context.field.name !== ""

		const fn = new Function("context", `return !!(${expression})`);
		const result = fn(context);
		return Boolean(result);
	} catch (error) {
		// If expression is invalid, show the field by default (fail-open)
		// Only log once per unique expression to avoid flooding console
		if (!loggedExpressionErrors.has(expression)) {
			loggedExpressionErrors.add(expression);
			console.warn(
				`Failed to evaluate visibility expression: ${expression}`,
				error,
			);
		}
		return true;
	}
}

/**
 * Filter query parameters based on form's allowed_query_params
 */
function filterQueryParams(
	queryParams: Record<string, string>,
	allowedParams: string[] | null | undefined,
): Record<string, string> {
	if (!allowedParams || allowedParams.length === 0) {
		return {};
	}

	const filtered: Record<string, string> = {};
	for (const param of allowedParams) {
		if (queryParams[param] !== undefined) {
			filtered[param] = queryParams[param];
		}
	}
	return filtered;
}

export function FormContextProvider({
	children,
	form,
	queryParams = {},
}: FormContextProviderProps) {
	const [isLoadingLaunchWorkflow, setIsLoadingLaunchWorkflow] =
		useState(false);
	const [startupHandle, setStartupHandle] = useState<string | null>(null);

	// Store workflow and field values in state (mutable parts)
	const [workflowResults, setWorkflowResultsState] = useState<
		Record<string, unknown>
	>({});
	const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});

	// Compute query params from props (derived value, not state)
	const filteredQuery = useMemo(
		() => filterQueryParams(queryParams, form.allowed_query_params),
		[queryParams, form.allowed_query_params],
	);

	// Combine into full context (memoized)
	const context = useMemo<FormContextValue>(
		() => ({
			workflow: workflowResults,
			query: filteredQuery,
			field: fieldValues,
		}),
		[workflowResults, filteredQuery, fieldValues],
	);

	const setWorkflowResults = useCallback(
		(results: Record<string, unknown>) => {
			setWorkflowResultsState(results);
		},
		[],
	);

	const setFieldValue = useCallback((fieldName: string, value: unknown) => {
		setFieldValues((prev) => ({
			...prev,
			[fieldName]: value,
		}));
	}, []);

	// Memoize isFieldVisible with context dependency
	const isFieldVisible = useCallback(
		(field: FormField): boolean => {
			// If no visibility expression, field is always visible
			if (!field.visibility_expression) {
				return true;
			}

			// Evaluate the expression with current context
			return evaluateVisibilityExpression(
				field.visibility_expression,
				context,
			);
		},
		[context],
	);

	// Memoize the provider value to prevent unnecessary re-renders
	const value: FormContextProviderValue = useMemo(
		() => ({
			context,
			setWorkflowResults,
			startupHandle,
			setStartupHandle,
			setFieldValue,
			isFieldVisible,
			isLoadingLaunchWorkflow,
			setIsLoadingLaunchWorkflow,
		}),
		[
			context,
			setWorkflowResults,
			startupHandle,
			setFieldValue,
			isFieldVisible,
			isLoadingLaunchWorkflow,
		],
	);

	return (
		<FormContext.Provider value={value}>{children}</FormContext.Provider>
	);
}

/**
 * Hook to access form context
 * Must be used within a FormContextProvider
 */
export function useFormContext() {
	const context = useContext(FormContext);
	if (context === undefined) {
		throw new Error(
			"useFormContext must be used within a FormContextProvider",
		);
	}
	return context;
}
