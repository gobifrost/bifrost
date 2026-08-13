import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type MemoryPlatformSettings =
	components["schemas"]["MemoryPlatformSettings"];
export type MemoryUserSettings = components["schemas"]["MemoryUserSettings"];
export type MemoryEntry = components["schemas"]["MemoryEntryPublic"];
export type MemoryEntryList = components["schemas"]["MemoryEntryList"];
export type MemoryDeleteResponse =
	components["schemas"]["MemoryDeleteResponse"];

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(
			typeof body.detail === "string"
				? body.detail
				: `Memory request failed: ${response.statusText}`,
		);
	}
	return response.json() as Promise<T>;
}

export function getPlatformMemorySettings(): Promise<MemoryPlatformSettings> {
	return request("/api/admin/memory/settings");
}

export function updatePlatformMemorySettings(
	enabled: boolean,
): Promise<MemoryPlatformSettings> {
	return request("/api/admin/memory/settings", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ enabled }),
	});
}

export function getUserMemorySettings(): Promise<MemoryUserSettings> {
	return request("/api/memory/settings");
}

export function updateUserMemorySettings(
	enabled: boolean,
): Promise<MemoryUserSettings> {
	return request("/api/memory/settings", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ enabled }),
	});
}

export function listMemories(): Promise<MemoryEntryList> {
	return request("/api/memory");
}

export function removeMemory(memoryId: string): Promise<MemoryDeleteResponse> {
	return request(`/api/memory/${memoryId}`, { method: "DELETE" });
}
