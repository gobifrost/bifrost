import { apiClient, authFetch } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

export type Solution = components["schemas"]["Solution"];
export type SolutionsList = components["schemas"]["SolutionsList"];
export type SolutionEntities = components["schemas"]["SolutionEntities"];
export type SolutionEntitySummary =
	components["schemas"]["SolutionEntitySummary"];
export type SolutionConfigStatus =
	components["schemas"]["SolutionConfigStatus"];
export type SolutionInstallPreview =
	components["schemas"]["SolutionInstallPreview"];
export type SolutionExistingInstall =
	components["schemas"]["SolutionExistingInstall"];
export type SolutionUpgradeDiff =
	components["schemas"]["SolutionUpgradeDiff"];
export type SolutionDeleteSummary =
	components["schemas"]["SolutionDeleteSummary"];
export type SolutionUpdate = components["schemas"]["SolutionUpdate"];
export type SolutionSetupStatus = components["schemas"]["SolutionSetupStatus"];
export type SolutionSetupItem = components["schemas"]["SolutionSetupItem"];
export type SolutionCaptureCandidates =
	components["schemas"]["SolutionCaptureCandidates"];
export type SolutionCaptureRequest =
	components["schemas"]["SolutionCaptureRequest"];
export type SolutionDependencyPreview =
	components["schemas"]["SolutionDependencyPreview"];
export type SolutionDependencyPreviewRequest =
	components["schemas"]["SolutionDependencyPreviewRequest"];
export type DependencyRef = components["schemas"]["DependencyRef"];
export type OutsideReference = components["schemas"]["OutsideReference"];
export type SolutionCaptureResponse =
	components["schemas"]["SolutionCaptureResponse"];
export type SolutionReadme = components["schemas"]["SolutionReadme"];
export type SolutionRepoPreviewRequest =
	components["schemas"]["SolutionRepoPreviewRequest"];

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

export async function getSolutionSetup(
	solutionId: string,
	options: RequestOptions = {},
): Promise<SolutionSetupStatus> {
	const { signal } = options;
	const { data, error } = await apiClient.GET(
		"/api/solutions/{solution_id}/setup",
		{ params: { path: { solution_id: solutionId } }, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to get solution setup status"));
	}
	return data;
}

export async function getSolutionReadme(
	solutionId: string,
	options: RequestOptions = {},
): Promise<SolutionReadme> {
	const { signal } = options;
	const { data, error } = await apiClient.GET(
		"/api/solutions/{solution_id}/readme",
		{ params: { path: { solution_id: solutionId } }, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to get solution readme"));
	}
	return data;
}

export async function putSolutionReadme(
	solutionId: string,
	readme: string | null,
	options: RequestOptions = {},
): Promise<SolutionReadme> {
	const { signal } = options;
	const { data, error } = await apiClient.PUT(
		"/api/solutions/{solution_id}/readme",
		{ params: { path: { solution_id: solutionId } }, body: { readme }, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to update solution readme"));
	}
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
 * Trigger a pull/sync of a git-connected install (the "Update now" action).
 * Pulls the latest commit at the install's configured ref and re-applies the
 * solution.
 */
export async function syncSolution(
	solutionId: string,
	options: RequestOptions = {},
): Promise<void> {
	const { signal } = options;
	const { error } = await apiClient.POST("/api/solutions/{solution_id}/sync", {
		params: { path: { solution_id: solutionId } },
		signal,
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to sync solution"));
}

/**
 * Preview a Solution install sourced from a git repository (parse-only). The
 * server clones the repo at the given ref/subpath and returns the same
 * `SolutionInstallPreview` shape as the zip-based `previewInstall`.
 */
export async function previewSolutionFromRepo(
	body: SolutionRepoPreviewRequest,
	options: RequestOptions = {},
): Promise<SolutionInstallPreview> {
	const { signal } = options;
	const { data, error } = await apiClient.POST(
		"/api/solutions/install/preview-repo",
		{ body, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to preview repository"));
	}
	return data;
}

/**
 * Install a Solution sourced from a git repository. The server clones the repo
 * at the given ref/subpath and installs it, returning the created `Solution`.
 */
export async function installSolutionFromRepo(
	body: SolutionRepoPreviewRequest,
	options: RequestOptions = {},
): Promise<Solution> {
	const { signal } = options;
	const { data, error } = await apiClient.POST(
		"/api/solutions/install/from-repo",
		{ body, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to install from repository"));
	}
	return data;
}

export async function getSolutionCaptureCandidates(
	solutionId: string,
	options: RequestOptions = {},
): Promise<SolutionCaptureCandidates> {
	const { signal } = options;
	const { data, error } = await apiClient.GET(
		"/api/solutions/{solution_id}/capture/candidates",
		{ params: { path: { solution_id: solutionId } }, signal },
	);
	if (error) {
		throw new Error(
			getErrorMessage(error, "Failed to list capture candidates"),
		);
	}
	return data;
}

export async function previewSolutionCapture(
	solutionId: string,
	request: SolutionDependencyPreviewRequest,
	options: RequestOptions = {},
): Promise<SolutionDependencyPreview> {
	const { signal } = options;
	const { data, error } = await apiClient.POST(
		"/api/solutions/{solution_id}/capture/preview",
		{ params: { path: { solution_id: solutionId } }, body: request, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to preview capture"));
	}
	return data;
}

export async function captureSolutionEntities(
	solutionId: string,
	request: SolutionCaptureRequest,
	options: RequestOptions = {},
): Promise<SolutionCaptureResponse> {
	const { signal } = options;
	const { data, error } = await apiClient.POST(
		"/api/solutions/{solution_id}/capture",
		{ params: { path: { solution_id: solutionId } }, body: request, signal },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to capture entities"));
	}
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

/**
 * Download the install's workspace zip. Defaults to "shareable" mode (strips
 * secrets). Pass mode="full" with a password to produce an encrypted full
 * backup that includes secret config values.
 */
export async function exportSolution(
	solutionId: string,
	mode: "shareable" | "full" = "shareable",
	password?: string,
	includeData?: boolean,
): Promise<{ blob: Blob; filename: string }> {
	// POST so the full-backup password rides in the request body, not the URL
	// query string (a query-string secret leaks into logs/proxies/history).
	// mode + include_data are not sensitive and stay in the query.
	const params = new URLSearchParams({ mode });
	if (includeData) params.set("include_data", "true");
	const response = await authFetch(
		`/api/solutions/${solutionId}/export?${params.toString()}`,
		{
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(password ? { password } : {}),
		},
	);
	if (!response.ok) {
		throw new Error(
			await parseUploadError(response, "Failed to export solution"),
		);
	}
	const disposition = response.headers.get("Content-Disposition") ?? "";
	const match = /filename="([^"]+)"/.exec(disposition);
	return {
		blob: await response.blob(),
		filename: match?.[1] ?? `solution-${solutionId}.zip`,
	};
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
 * Preview a Solution install zip (parse-only). Posts a multipart `file` and an
 * optional `organization_id` (empty/absent = global) so the server can match
 * an existing install at that scope and return `existing_install` + `diff`.
 */
export async function previewInstall(
	file: File,
	params: { organizationId?: string } = {},
	options: RequestOptions = {},
): Promise<SolutionInstallPreview> {
	const formData = new FormData();
	formData.append("file", file);
	formData.append("organization_id", params.organizationId ?? "");

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
 * Pass `force: true` to override the server's downgrade guard (409 when the
 * package version is older than the installed version).
 *
 * `replaceSecrets: true` — re-install overwriting existing secret config
 *   values (send when the user confirms the collision prompt on 409).
 * `replaceData: true` — re-install overwriting existing table data.
 *
 * NOTE: `password` for full-backup installs is NOT yet wired into the install
 * UI. The server will return 422 if a full-backup zip is uploaded without the
 * correct password field. A future task should add a password prompt to the
 * install flow (CreateEditSolution / update dialog) when the preview response
 * indicates a full-backup zip. For now, the gap is documented here and in the
 * SolutionDetail import-collision handler.
 */
export async function installSolution(
	params: {
		file: File;
		organizationId?: string;
		configValues?: Record<string, unknown>;
		force?: boolean;
		replaceSecrets?: boolean;
		replaceData?: boolean;
		password?: string;
	},
	options: RequestOptions = {},
): Promise<Solution> {
	const {
		file,
		organizationId,
		configValues,
		force,
		replaceSecrets,
		replaceData,
		password,
	} = params;
	const formData = new FormData();
	formData.append("file", file);
	formData.append("organization_id", organizationId ?? "");
	formData.append("config_values", JSON.stringify(configValues ?? {}));
	if (replaceSecrets) formData.append("replace_secrets", "true");
	if (replaceData) formData.append("replace_data", "true");
	if (password) formData.append("password", password);

	const url = force
		? "/api/solutions/install?force=true"
		: "/api/solutions/install";
	const response = await authFetch(url, {
		method: "POST",
		body: formData,
		signal: options.signal,
	});
	if (!response.ok) {
		const err = new Error(
			await parseUploadError(
				response,
				`Failed to install solution: ${response.statusText}`,
			),
		);
		// Attach status so callers can branch on 409 / 422 without re-parsing.
		(err as Error & { status?: number }).status = response.status;
		throw err;
	}
	return response.json();
}
