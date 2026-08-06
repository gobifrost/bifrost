/**
 * Data Providers hooks and utilities
 *
 * Data providers are a special type of workflow (type='data_provider') that return
 * options for form fields and other dynamic data sources.
 *
 * - Listing: Use GET /api/workflows?type=data_provider
 * - Other admin surfaces: Use POST /api/workflows/execute
 * - Form runtime: Use the field-derived form options endpoint
 */

import { $api, apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Auto-generated types from OpenAPI spec
export type DataProvider = components["schemas"]["WorkflowMetadata"];

// Types for data provider options
export type DataProviderOption = {
	label: string;
	value: string;
	description?: string;
	metadata?: Record<string, unknown>;
};

/**
 * Hook to fetch all available data providers
 *
 * Data providers are workflows with type='data_provider', so we use the
 * workflows endpoint with a type filter.
 */
export function useDataProviders() {
	return $api.useQuery("get", "/api/workflows", {
		params: { query: { type: "data_provider" } },
	});
}

/**
 * Standalone async function to get options from a data provider
 *
 * Uses the field-derived form runtime endpoint; the browser never receives or
 * sends the underlying provider workflow ID.
 *
 * @param formId - Runtime form UUID
 * @param fieldName - Field whose persisted provider should run
 * @param inputs - Optional input parameters for the data provider
 */
export async function getFormFieldOptions(
	formId: string,
	fieldName: string,
	inputs?: Record<string, unknown>,
): Promise<DataProviderOption[]> {
	try {
		const { data, error } = await apiClient.POST(
			"/api/forms/{form_id}/fields/{field_name}/options",
			{
				params: { path: { form_id: formId, field_name: fieldName } },
				body: { inputs: inputs || {} },
			},
		);

		if (error || !data) {
			console.error("Failed to invoke data provider:", error);
			return [];
		}

		return data.options.map((opt) => ({
			value: opt.value,
			label: opt.label,
			...(opt.description ? { description: opt.description } : {}),
			...(opt.metadata ? { metadata: opt.metadata } : {}),
		}));
	} catch (error) {
		console.error("Error invoking data provider:", error);
		return [];
	}
}

/** Execute a provider directly for authenticated non-form administration. */
export async function getDataProviderOptions(
	providerId: string,
	inputs?: Record<string, unknown>,
): Promise<DataProviderOption[]> {
	try {
		const { data, error } = await apiClient.POST("/api/workflows/execute", {
			body: {
				workflow_id: providerId,
				input_data: inputs || {},
				transient: true,
			},
		});
		if (error || !data || data.status !== "Success") return [];
		const options = data.result as Array<{
			value?: string;
			label?: string;
			description?: string;
			metadata?: Record<string, unknown>;
		}> | null;
		if (!Array.isArray(options)) return [];
		return options.map((option) => ({
			value: String(option.value ?? ""),
			label: String(option.label ?? option.value ?? ""),
			...(option.description ? { description: option.description } : {}),
			...(option.metadata ? { metadata: option.metadata } : {}),
		}));
	} catch {
		return [];
	}
}
