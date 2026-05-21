/**
 * React Query hooks for forms management using openapi-react-query pattern
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
import type { FormSubmission, FormExecutionResponse } from "@/lib/client-types";

import { toast } from "sonner";

type FormCreate = components["schemas"]["FormCreate"];
type FormUpdate = components["schemas"]["FormUpdate"];
type FormPublic = components["schemas"]["FormPublic"];
type FormStartupResponse = components["schemas"]["FormStartupResponse"];

/** Helper to extract error message from API error response */
function getErrorMessage(error: unknown, fallback: string): string {
	if (typeof error === "object" && error && "message" in error) {
		return String((error as Record<string, unknown>)["message"]);
	}
	return fallback;
}

/**
 * Get all forms
 */
export async function getForms(): Promise<FormPublic[]> {
	const { data, error } = await apiClient.GET("/api/forms");
	if (error) throw new Error(getErrorMessage(error, "Failed to fetch forms"));
	return data || [];
}

/**
 * Get a specific form by ID
 */
export async function getForm(formId: string): Promise<FormPublic> {
	const { data, error } = await apiClient.GET("/api/forms/{form_id}", {
		params: { path: { form_id: formId } },
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to fetch form"));
	return data!;
}

/**
 * Create a new form
 */
export async function createForm(request: FormCreate): Promise<FormPublic> {
	const { data, error } = await apiClient.POST("/api/forms", {
		body: request,
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to create form"));
	return data!;
}

/**
 * Update a form
 */
export async function updateForm(
	formId: string,
	request: FormUpdate,
): Promise<FormPublic> {
	const { data, error } = await apiClient.PATCH("/api/forms/{form_id}", {
		params: { path: { form_id: formId } },
		body: request,
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to update form"));
	return data!;
}

/**
 * Delete a form (soft delete - sets isActive=false)
 */
export async function deleteForm(formId: string): Promise<void> {
	const { error } = await apiClient.DELETE("/api/forms/{form_id}", {
		params: { path: { form_id: formId } },
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to delete form"));
}

/**
 * Execute a form to run workflow
 */
export async function submitForm(
	submission: FormSubmission,
): Promise<FormExecutionResponse> {
	const { data, error } = await apiClient.POST(
		"/api/forms/{form_id}/execute",
		{
			params: { path: { form_id: submission.form_id } },
			body: submission.form_data,
		},
	);
	if (error || !data) {
		throw new Error(getErrorMessage(error, "Failed to submit form"));
	}
	return data as FormExecutionResponse;
}

/**
 * Execute form startup workflow
 * Runs the launch workflow before the form is displayed to populate initial context
 */
export async function executeFormStartup(
	formId: string,
	inputData: Record<string, unknown> = {},
): Promise<FormStartupResponse> {
	const { data, error } = await apiClient.POST(
		"/api/forms/{form_id}/startup",
		{
			params: { path: { form_id: formId } },
			body: inputData,
		},
	);
	if (error || !data) {
		throw new Error(
			getErrorMessage(error, "Failed to execute startup workflow"),
		);
	}
	return data;
}

/**
 * Query hook to fetch all forms with optional organization filtering
 * @param filterScope - Filter scope: undefined = all, null = global only, string = org UUID
 *
 * The scope query param controls filtering:
 * - Omitted (undefined): show all forms (superusers) / user's org + global (org users)
 * - "global": show only global forms (org_id IS NULL)
 * - UUID string: show that org's forms + global forms
 */
export function useForms(filterScope?: string | null, options?: { enabled?: boolean }) {
	// Build query params - scope is the new filter parameter
	const queryParams: Record<string, string | undefined> = {};
	if (filterScope === null) {
		// null means "global only"
		queryParams.scope = "global";
	} else if (filterScope !== undefined) {
		// UUID string means specific org
		queryParams.scope = filterScope;
	}
	// undefined = don't send scope (show all)

	return $api.useQuery("get", "/api/forms", {
		params: {
			// Type assertion needed until types are regenerated
			query:
				Object.keys(queryParams).length > 0 ? queryParams : undefined,
		} as { query?: { scope?: string } },
	}, {
		enabled: options?.enabled,
	});
}

/**
 * Query hook to fetch a single form by ID
 */
export function useForm(formId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/forms/{form_id}",
		{ params: { path: { form_id: formId ?? "" } } },
		{ enabled: !!formId },
	);
}

/**
 * Mutation hook to create a form
 */
export function useCreateForm() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/forms", {
		onSuccess: (_responseData, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/forms"] });
			const name = (variables.body as FormCreate)?.name;
			toast.success("Form created", {
				description: `Form "${name}" has been created`,
			});
		},
		onError: (error) => {
			toast.error("Failed to create form", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Mutation hook to update a form
 */
export function useUpdateForm() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/forms/{form_id}", {
		onSuccess: (_responseData, variables) => {
			const formId = (variables.params as { path: { form_id: string } })
				.path.form_id;
			queryClient.invalidateQueries({ queryKey: ["get", "/api/forms"] });
			queryClient.invalidateQueries({
				queryKey: [
					"get",
					"/api/forms/{form_id}",
					{ params: { path: { form_id: formId } } },
				],
			});
			toast.success("Form updated", {
				description: "The form has been updated successfully",
			});
		},
		onError: (error) => {
			toast.error("Failed to update form", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Mutation hook to delete a form
 */
export function useDeleteForm() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/forms/{form_id}", {
		onSuccess: (_data, variables) => {
			queryClient.invalidateQueries({ queryKey: ["get", "/api/forms"] });
			const purged = variables?.params?.query?.purge;
			toast.success(purged ? "Form permanently removed" : "Form deleted", {
				description: purged
					? "The form has been permanently removed from the database"
					: "The form has been deactivated",
			});
		},
		onError: (error) => {
			toast.error("Failed to delete form", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Mutation hook to submit a form and execute workflow
 */
export function useSubmitForm() {
	return $api.useMutation("post", "/api/forms/{form_id}/execute", {
		onSuccess: (responseData) => {
			toast.success("Workflow execution started", {
				description: `Execution ID: ${(responseData as FormExecutionResponse).execution_id}`,
			});
		},
		onError: (error) => {
			toast.error("Failed to submit form", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}
