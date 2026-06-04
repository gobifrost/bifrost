/**
 * User invite service wrappers.
 *
 * Thin imperative wrappers around the invite endpoints; in React components
 * prefer the typed hooks in `@/hooks/useUserInvites`.
 */

import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type CreateInviteResponse =
	components["schemas"]["CreateInviteResponse"];

export async function resendInvite(
	userId: string,
): Promise<CreateInviteResponse> {
	const { data, error } = await apiClient.POST(
		"/api/users/{user_id}/invite/resend",
		{ params: { path: { user_id: userId } } },
	);
	if (error) throw error;
	return data as CreateInviteResponse;
}

export async function regenerateInvite(
	userId: string,
): Promise<CreateInviteResponse> {
	const { data, error } = await apiClient.POST(
		"/api/users/{user_id}/invite/regenerate",
		{ params: { path: { user_id: userId } } },
	);
	if (error) throw error;
	return data as CreateInviteResponse;
}

export async function sendInviteEmail(
	userId: string,
	registrationUrl: string,
): Promise<CreateInviteResponse> {
	const { data, error } = await apiClient.POST(
		"/api/users/{user_id}/invite/send",
		{
			params: { path: { user_id: userId } },
			body: { registration_url: registrationUrl },
		},
	);
	if (error) throw error;
	return data as CreateInviteResponse;
}

export async function revokeInvite(userId: string): Promise<void> {
	const { error } = await apiClient.DELETE("/api/users/{user_id}/invite", {
		params: { path: { user_id: userId } },
	});
	if (error) throw error;
}
