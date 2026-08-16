/**
 * React Query hooks for applications management
 *
 * Handles CRUD operations for App Builder applications.
 */

import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

// Re-export types from OpenAPI spec
export type ApplicationPublic = components["schemas"]["ApplicationPublic"];
export type ApplicationCreate = components["schemas"]["ApplicationCreate"];
export type ApplicationUpdate = components["schemas"]["ApplicationUpdate"];
export type ApplicationListResponse =
	components["schemas"]["ApplicationListResponse"];
export type ApplicationPublishRequest =
	components["schemas"]["ApplicationPublishRequest"];
export type PlatformJobAccepted =
	components["schemas"]["PlatformJobAccepted"];

// Export type for applications
export type ApplicationExport = ApplicationPublic;

/** Helper to extract error message from API error response */
function getErrorMessage(error: unknown, fallback: string): string {
	if (typeof error === "object" && error) {
		const errObj = error as Record<string, unknown>;
		// Check for message
		if ("message" in errObj && typeof errObj.message === "string") {
			return errObj.message;
		}
		// Check for FastAPI detail format
		if ("detail" in errObj) {
			const detail = errObj.detail;
			if (typeof detail === "string") {
				return detail;
			}
			// FastAPI validation errors come as array of objects
			if (Array.isArray(detail)) {
				return detail
					.map((d) => {
						if (typeof d === "object" && d && "msg" in d) {
							const loc =
								"loc" in d && Array.isArray(d.loc)
									? d.loc.join(".")
									: "";
							return loc ? `${loc}: ${d.msg}` : String(d.msg);
						}
						return JSON.stringify(d);
					})
					.join("; ");
			}
			return JSON.stringify(detail);
		}
	}
	return fallback;
}

// =============================================================================
// Application Hooks
// =============================================================================

/**
 * Hook to fetch all applications
 */
export function useApplications(scope?: string) {
	return $api.useQuery(
		"get",
		"/api/applications",
		scope ? { params: { query: { scope } } } : undefined,
	);
}

/**
 * Hook to fetch a single application by slug (globally unique)
 */
export function useApplication(slug: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/applications/{slug}",
		{
			params: {
				path: { slug: slug ?? "" },
			},
		},
		{ enabled: !!slug, staleTime: 60_000 },
	);
}

/**
 * Hook to create a new application
 */
export function useCreateApplication() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/applications", {
		onSuccess: (data) => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications"],
			});
			toast.success("Application created", {
				description: `Application "${data.name}" has been created`,
			});
		},
		onError: (error) => {
			toast.error("Failed to create application", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Hook to update application metadata
 */
export function useUpdateApplication() {
	const queryClient = useQueryClient();

	return $api.useMutation("patch", "/api/applications/{app_id}", {
		onSuccess: (data) => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications/{slug}"],
			});
			toast.success("Application updated", {
				description: `Application "${data.name}" has been updated`,
			});
		},
		onError: (error) => {
			toast.error("Failed to update application", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Hook to repoint an application's source directory (repo_path).
 */
export function useReplaceApplication() {
	const queryClient = useQueryClient();

	return $api.useMutation("post", "/api/applications/{app_id}/replace", {
		onSuccess: (data) => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications"],
			});
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications/{slug}"],
			});
			toast.success("Path replaced", {
				description: `"${data.name}" now points to ${data.repo_path}`,
			});
		},
		onError: (error) => {
			toast.error("Failed to replace path", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Hook to validate an application's source files.
 */
export function useValidateApplication() {
	return $api.useMutation("post", "/api/applications/{app_id}/validate");
}

/**
 * Hook to delete an application
 */
export function useDeleteApplication() {
	const queryClient = useQueryClient();

	return $api.useMutation("delete", "/api/applications/{app_id}", {
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/applications"],
			});
			toast.success("Application deleted");
		},
		onError: (error) => {
			toast.error("Failed to delete application", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/**
 * Queue application publishing. Live progress and the terminal result are
 * delivered by the existing notification WebSocket channel.
 */
export function usePublishApplication() {
	return $api.useMutation("post", "/api/applications/{app_id}/publish", {
		onSuccess: (operation) => {
			toast.success(
				operation.reused
					? "Following existing application publish"
					: "Application publish queued",
				{
					description: operation.notification_id
						? "Progress will appear in notifications."
						: `Track durable job ${operation.job_id}.`,
				},
			);
		},
		onError: (error) => {
			toast.error("Failed to queue application publish", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

// =============================================================================
// Export Hook
// =============================================================================

/**
 * Hook to export an application to JSON
 * @param appId - Application ID
 * @param versionId - Version ID (optional, defaults to current draft)
 */
export function useExportApplication(
	appId: string | undefined,
	versionId: string | undefined,
) {
	return $api.useQuery(
		"get",
		"/api/applications/{app_id}/export",
		{
			params: {
				path: { app_id: appId ?? "" },
				query: {
					version_id: versionId,
				},
			},
		},
		{ enabled: !!appId && !!versionId },
	);
}

// =============================================================================
// Imperative API Functions (for use outside React components)
// =============================================================================

/**
 * List applications (imperative)
 */
export async function listApplications(
	scope?: string,
): Promise<ApplicationListResponse> {
	const { data, error } = await apiClient.GET("/api/applications", {
		params: {
			query: scope ? { scope } : undefined,
		},
	});
	if (error)
		throw new Error(getErrorMessage(error, "Failed to list applications"));
	return data;
}

/**
 * Get an application by slug (imperative)
 */
export async function getApplication(
	slug: string,
): Promise<ApplicationPublic> {
	const { data, error } = await apiClient.GET("/api/applications/{slug}", {
		params: {
			path: { slug },
		},
	});
	if (error)
		throw new Error(getErrorMessage(error, "Failed to get application"));
	return data;
}

/**
 * Create an application (imperative)
 */
export async function createApplication(
	appData: ApplicationCreate,
): Promise<ApplicationPublic> {
	const { data, error } = await apiClient.POST("/api/applications", {
		body: appData,
	});
	if (error)
		throw new Error(getErrorMessage(error, "Failed to create application"));
	return data;
}

/**
 * Update an application (imperative)
 */
export async function updateApplication(
	appId: string,
	appData: ApplicationUpdate,
): Promise<ApplicationPublic> {
	const { data, error } = await apiClient.PATCH(
		"/api/applications/{app_id}",
		{
			params: {
				path: { app_id: appId },
			},
			body: appData,
		},
	);
	if (error)
		throw new Error(getErrorMessage(error, "Failed to update application"));
	return data;
}

/**
 * Delete an application (imperative)
 */
export async function deleteApplication(appId: string): Promise<void> {
	const { error } = await apiClient.DELETE("/api/applications/{app_id}", {
		params: {
			path: { app_id: appId },
		},
	});
	if (error)
		throw new Error(getErrorMessage(error, "Failed to delete application"));
}

/**
 * Publish application (imperative)
 */
export async function publishApplication(
	appId: string,
	message?: string,
): Promise<PlatformJobAccepted> {
	const { data, error } = await apiClient.POST(
		"/api/applications/{app_id}/publish",
		{
			params: { path: { app_id: appId } },
			body: { message: message || null },
		},
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to queue application publish"));
	}
	return data;
}

/**
 * Export application (imperative)
 * @param appId - Application ID
 * @param versionId - Version ID (optional, defaults to current draft)
 */
export async function exportApplication(
	appId: string,
	versionId: string,
): Promise<ApplicationExport> {
	const { data, error } = await apiClient.GET(
		"/api/applications/{app_id}/export",
		{
			params: {
				path: { app_id: appId },
				query: {
					version_id: versionId,
				},
			},
		},
	);
	if (error)
		throw new Error(getErrorMessage(error, "Failed to export application"));
	return data;
}
