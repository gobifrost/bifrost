import { authFetch } from "@/lib/api-client";

export interface ArtifactRetentionSettings {
	enabled: boolean;
	retention_days: number;
}

export interface ArtifactRetentionCleanupJob {
	job_id: string;
	status: string;
	reused: boolean;
	notification_id: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(
			typeof body.detail === "string"
				? body.detail
				: `Artifact retention request failed: ${response.statusText}`,
		);
	}
	return response.json() as Promise<T>;
}

export function getArtifactRetentionSettings(): Promise<ArtifactRetentionSettings> {
	return request("/api/maintenance/artifact-retention/settings");
}

export function updateArtifactRetentionSettings(
	settings: ArtifactRetentionSettings,
): Promise<ArtifactRetentionSettings> {
	return request("/api/maintenance/artifact-retention/settings", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(settings),
	});
}

export function cleanupExpiredArtifacts(): Promise<ArtifactRetentionCleanupJob> {
	return request("/api/maintenance/artifact-retention/cleanup", {
		method: "POST",
	});
}
