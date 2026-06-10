import { apiClient, authFetch } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

export type Solution = components["schemas"]["Solution"];
export type SolutionsList = components["schemas"]["SolutionsList"];
export type SolutionEntities = components["schemas"]["SolutionEntities"];
export type SolutionInstallPreview =
	components["schemas"]["SolutionInstallPreview"];
export type SolutionDeleteSummary =
	components["schemas"]["SolutionDeleteSummary"];
export type SolutionUpdate = components["schemas"]["SolutionUpdate"];

interface RequestOptions {
	signal?: AbortSignal;
}

export async function listSolutions(
	options: RequestOptions = {},
): Promise<SolutionsList> {
	const { signal } = options;
	const { data, error } = await apiClient.GET("/api/solutions", { signal });
	if (error) throw new Error(getErrorMessage(error, "Failed to list solutions"));
	return data;
}

export async function getSolution(
	solutionId: string,
	options: RequestOptions = {},
): Promise<Solution> {
	const { signal } = options;
	const { data, error } = await apiClient.GET("/api/solutions/{solution_id}", {
		params: { path: { solution_id: solutionId } },
		signal,
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to get solution"));
	return data;
}

export async function getSolutionEntities(
	solutionId: string,
	options: RequestOptions = {},
): Promise<SolutionEntities> {
	const { signal } = options;
	const { data, error } = await apiClient.GET(
		"/api/solutions/{solution_id}/entities",
		{ params: { path: { solution_id: solutionId } }, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to get solution entities"));
	}
	return data;
}

export async function updateSolution(
	solutionId: string,
	update: SolutionUpdate,
	options: RequestOptions = {},
): Promise<Solution> {
	const { signal } = options;
	const { data, error } = await apiClient.PATCH(
		"/api/solutions/{solution_id}",
		{ params: { path: { solution_id: solutionId } }, body: update, signal },
	);
	if (error) throw new Error(getErrorMessage(error, "Failed to update solution"));
	return data;
}

/**
 * Set a config VALUE for a Solution install's org scope. Config values are
 * instance-owned `Config` rows (never part of the portable declaration), so we
 * write them through the existing `/api/config` endpoint scoped to the install's
 * organization. `organizationId` is the install's org (`null` for a global install).
 */
export async function setSolutionConfig(
	params: {
		key: string;
		value: string;
		type: components["schemas"]["ConfigType"];
		organizationId: string | null;
	},
	options: RequestOptions = {},
): Promise<void> {
	const { key, value, type, organizationId } = params;
	const { error } = await apiClient.POST("/api/config", {
		body: { key, value, type, organization_id: organizationId },
		signal: options.signal,
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to save config value"));
}

export async function deleteSolution(
	solutionId: string,
	options: RequestOptions = {},
): Promise<SolutionDeleteSummary> {
	const { signal } = options;
	const { data, error } = await apiClient.DELETE(
		"/api/solutions/{solution_id}",
		{ params: { path: { solution_id: solutionId } }, signal },
	);
	if (error) throw new Error(getErrorMessage(error, "Failed to delete solution"));
	return data;
}

async function parseUploadError(
	response: Response,
	fallback: string,
): Promise<string> {
	const body = await response.json().catch(() => ({}));
	if (body && typeof body.detail === "string") {
		return body.detail;
	}
	return fallback;
}

/**
 * Preview a Solution install zip (parse-only). Posts a multipart `file`.
 */
export async function previewInstall(
	file: File,
	options: RequestOptions = {},
): Promise<SolutionInstallPreview> {
	const formData = new FormData();
	formData.append("file", file);

	const response = await authFetch("/api/solutions/install/preview", {
		method: "POST",
		body: formData,
		signal: options.signal,
	});
	if (!response.ok) {
		throw new Error(
			await parseUploadError(
				response,
				`Failed to preview install: ${response.statusText}`,
			),
		);
	}
	return response.json();
}

/**
 * Install a Solution zip. Posts a multipart `file`, optional `organization_id`
 * (empty string installs globally), and `config_values` (JSON-encoded map).
 */
export async function installSolution(
	params: {
		file: File;
		organizationId?: string;
		configValues?: Record<string, unknown>;
	},
	options: RequestOptions = {},
): Promise<Solution> {
	const { file, organizationId, configValues } = params;
	const formData = new FormData();
	formData.append("file", file);
	formData.append("organization_id", organizationId ?? "");
	formData.append("config_values", JSON.stringify(configValues ?? {}));

	const response = await authFetch("/api/solutions/install", {
		method: "POST",
		body: formData,
		signal: options.signal,
	});
	if (!response.ok) {
		throw new Error(
			await parseUploadError(
				response,
				`Failed to install solution: ${response.statusText}`,
			),
		);
	}
	return response.json();
}
