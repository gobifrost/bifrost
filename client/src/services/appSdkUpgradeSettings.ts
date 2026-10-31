import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type AppSdkUpgradeSettings =
	components["schemas"]["AppSdkUpgradeSettings"];

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(
			typeof body.detail === "string"
				? body.detail
				: `App SDK upgrade settings request failed: ${response.statusText}`,
		);
	}
	return response.json() as Promise<T>;
}

export function getAppSdkUpgradeSettings(): Promise<AppSdkUpgradeSettings> {
	return request("/api/maintenance/app-sdk-upgrades/settings");
}

export function updateAppSdkUpgradeSettings(
	enabled: boolean,
): Promise<AppSdkUpgradeSettings> {
	return request("/api/maintenance/app-sdk-upgrades/settings", {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ enabled }),
	});
}
