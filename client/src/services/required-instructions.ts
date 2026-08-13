import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type RequiredInstructionsSettings =
	components["schemas"]["RequiredInstructionsSettings"];

async function request(
	url: string,
	init?: RequestInit,
): Promise<RequiredInstructionsSettings> {
	const response = await authFetch(url, init);
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(
			typeof body.detail === "string"
				? body.detail
				: `Required instructions request failed: ${response.statusText}`,
		);
	}
	return response.json() as Promise<RequiredInstructionsSettings>;
}

function settingsUrl(organizationId?: string): string {
	return organizationId
		? `/api/admin/required-instructions/organizations/${organizationId}`
		: "/api/admin/required-instructions";
}

export function getRequiredInstructionsSettings(
	organizationId?: string,
): Promise<RequiredInstructionsSettings> {
	return request(settingsUrl(organizationId));
}

export function updateRequiredInstructionsSettings(
	instructions: string,
	organizationId?: string,
): Promise<RequiredInstructionsSettings> {
	return request(settingsUrl(organizationId), {
		method: "PUT",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ instructions }),
	});
}
