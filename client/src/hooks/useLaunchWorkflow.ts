/**
 * Hook to execute launch workflow on form load
 *
 * When a form has a launchWorkflowId, this hook:
 * 1. Executes the workflow when the form loads
 * 2. Extracts results into form context
 * 3. Enables field visibility based on workflow results
 */

import { useEffect, useMemo, useState } from "react";
import { useFormContext } from "@/contexts/FormContext";
import { executeFormStartup } from "@/hooks/useForms";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

type Form =
	| components["schemas"]["FormPublic"]
	| components["schemas"]["FormRuntimeDefinition"];

interface UseLaunchWorkflowOptions {
	orgId?: string;
	form: Form;
	/** Additional parameters to pass to launch workflow */
	workflowParams?: Record<string, unknown>;
}

/**
 * Execute launch workflow if form has launchWorkflowId
 */
export function useLaunchWorkflow({
	form,
	orgId,
	workflowParams = {},
}: UseLaunchWorkflowOptions) {
	const [error, setError] = useState<string | null>(null);
	const [attempt, setAttempt] = useState(0);
	const {
		context,
		setWorkflowResults,
		setStartupHandle,
		setIsLoadingLaunchWorkflow,
	} = useFormContext();
	const hasStartup =
		"has_startup" in form
			? form.has_startup
			: Boolean(form.launch_workflow_id);

	// Memoize serialized objects for dependency comparison
	const serializedQuery = useMemo(
		() => JSON.stringify(context.query),
		[context.query],
	);
	const serializedParams = useMemo(
		() => JSON.stringify(workflowParams),
		[workflowParams],
	);

	useEffect(() => {
		// Only execute if form has a launch workflow configured
		if (!hasStartup) {
			setIsLoadingLaunchWorkflow(false);
			setStartupHandle(null);
			return;
		}

		let cancelled = false;
		const executeLaunchWorkflow = async () => {
			try {
				setError(null);
				setIsLoadingLaunchWorkflow(true);

				// The server owns default parameters. The browser may send only the
				// query names exposed by the runtime definition.
				const inputData = {
					...context.query,
					...workflowParams,
				};

				// Execute the startup workflow
				const response = await executeFormStartup(
					form.id,
					inputData,
					orgId,
				);
				if (cancelled) return;

				// Set workflow results in context (or empty object if no result)
				setWorkflowResults(
					(response.result as Record<string, unknown>) || {},
				);
				setStartupHandle(response.startup_handle || null);
			} catch (error) {
				if (cancelled) return;
				setError(getErrorMessage(error, "Please try again."));
				// Do not leave stale startup data available after a failed reload.
				setWorkflowResults({});
				setStartupHandle(null);
			} finally {
				if (!cancelled) setIsLoadingLaunchWorkflow(false);
			}
		};

		void executeLaunchWorkflow();
		return () => {
			cancelled = true;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [
		form.id,
		orgId,
		attempt,
		hasStartup,
		serializedQuery,
		serializedParams, // Serialized to track changes without object identity issues
		setWorkflowResults,
		setStartupHandle,
		setIsLoadingLaunchWorkflow,
	]);
	return {
		error: hasStartup ? error : null,
		retry: () => {
			setError(null);
			setAttempt((current) => current + 1);
		},
	};
}
