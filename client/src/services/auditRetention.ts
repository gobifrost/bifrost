import { authFetch } from "@/lib/api-client";
import { ApiError, parseApiError } from "@/lib/api-error";
import type { components } from "@/lib/v1";
import { observePlatformJob, type PlatformJob } from "@/services/platformJobs";
import { webSocketService } from "@/services/websocket";

export type AuditRetentionStatus =
	components["schemas"]["AuditRetentionStatus"];
export type AuditRetentionInfo = components["schemas"]["AuditRetentionInfo"];
export type AuditRetentionSettingsUpdate =
	components["schemas"]["AuditRetentionSettingsUpdate"];
export type AuditArchiveRunRequest =
	components["schemas"]["AuditArchiveRunRequest"];
export type AuditExpiryPreview = components["schemas"]["AuditExpiryPreview"];
export type AuditExportRequest = components["schemas"]["AuditExportRequest"];
export type PlatformJobAccepted = components["schemas"]["PlatformJobAccepted"];

/**
 * The fields of a dry run's result that the settings card shows. The job's
 * `result` is untyped in the OpenAPI schema; the server builds it in
 * `api/src/services/audit_retention/archiver.py::plan_archive`.
 */
export interface AuditArchivePlan {
	eligible_rows: number;
	days: { day: string; rows: number; bytes: number }[];
	expiring_rows: number;
}

/** The server's reason for a refused request, with its status code. */
async function requestError(response: Response): Promise<ApiError> {
	const body = await response.json().catch(() => null);
	return body && typeof body === "object" && "detail" in body
		? parseApiError(body, response.status)
		: new ApiError(
				`Audit retention request failed: ${response.statusText}`,
				response.status,
			);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) throw await requestError(response);
	return response.json() as Promise<T>;
}

function postJson(body: unknown, method = "POST"): RequestInit {
	return {
		method,
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body),
	};
}

export function getAuditRetention(): Promise<AuditRetentionStatus> {
	return request("/api/maintenance/audit-retention/settings");
}

export function updateAuditRetention(
	body: AuditRetentionSettingsUpdate,
): Promise<AuditRetentionStatus> {
	return request(
		"/api/maintenance/audit-retention/settings",
		postJson(body, "PUT"),
	);
}

export function runAuditArchive(
	body: AuditArchiveRunRequest,
): Promise<PlatformJobAccepted> {
	return request("/api/maintenance/audit-retention/run", postJson(body));
}

/** What an archive window would delete; null previews keeping archives forever. */
export function previewAuditExpiry(
	archiveDays: number | null,
): Promise<AuditExpiryPreview> {
	const query = archiveDays === null ? "" : `?archive_days=${archiveDays}`;
	return request(`/api/maintenance/audit-retention/preview${query}`);
}

export function createAuditExport(
	body: AuditExportRequest,
): Promise<PlatformJobAccepted> {
	return request("/api/audit/exports", postJson(body));
}

export function auditExportDownloadPath(jobId: string): string {
	return `/api/audit/exports/${jobId}/download`;
}

export async function downloadAuditExport(
	jobId: string,
	filename: string,
): Promise<void> {
	const response = await authFetch(auditExportDownloadPath(jobId));
	if (!response.ok) throw await requestError(response);
	const blobUrl = URL.createObjectURL(await response.blob());
	try {
		const anchor = document.createElement("a");
		anchor.href = blobUrl;
		anchor.download = filename;
		anchor.click();
	} finally {
		URL.revokeObjectURL(blobUrl);
	}
}

/**
 * Follow a retention or export job over the notification WebSocket. One
 * status snapshot closes the accepted-response-to-notification race without
 * polling. The returned function stops both.
 */
export function watchAuditJob(
	jobId: string,
	onUpdate: (job: PlatformJob) => void,
): () => void {
	const unsubscribe = webSocketService.onPlatformJobUpdate(jobId, onUpdate);
	const observation = observePlatformJob(jobId, onUpdate);
	// Notification delivery stays authoritative when the snapshot fails.
	void observation.promise.catch(() => undefined);
	return () => {
		unsubscribe();
		observation.cancel();
	};
}
