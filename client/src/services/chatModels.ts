import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type ChatModelProfileCapabilities = components["schemas"]["ModelCapabilities"];
export type ChatModelProfileOption = components["schemas"]["ChatModelProfilePublic"];
export type ChatModelProfilesResponse = components["schemas"]["ChatModelProfilesResponse"];

export type ChatModelProfileId = ChatModelProfileOption["id"];

export async function getChatModelProfiles(): Promise<ChatModelProfilesResponse> {
	const { data, error } = await apiClient.GET(
		"/api/chat/model-profiles",
	);
	if (error) {
		throw new Error("Failed to fetch chat model profiles");
	}
	return (data ?? {
		profiles: [],
		default_profile_id: null,
	});
}
