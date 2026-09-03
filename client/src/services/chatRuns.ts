import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type ChatRunCreateRequest =
	components["schemas"]["ChatRunCreateRequest"];
export type ChatRunCreateResponse =
	components["schemas"]["ChatRunCreateResponse"];
export type ChatRunStateResponse =
	components["schemas"]["ChatRunStateResponse"];
export type ChatRunCancelResponse =
	components["schemas"]["ChatRunCancelResponse"];

function getErrorMessage(error: unknown, fallback: string): string {
	if (typeof error === "object" && error && "message" in error) {
		return String((error as Record<string, unknown>).message);
	}
	return error instanceof Error ? error.message : fallback;
}

export async function createChatRun(
	request: ChatRunCreateRequest,
): Promise<ChatRunCreateResponse> {
	const { data, error } = await apiClient.POST("/api/chat/runs", {
		body: request,
	});
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to send message"));
	}
	return data;
}

export async function getChatRunState(
	conversationId: string,
): Promise<ChatRunStateResponse> {
	const { data, error } = await apiClient.GET(
		"/api/chat/conversations/{conversation_id}/state",
		{ params: { path: { conversation_id: conversationId } } },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to restore chat state"));
	}
	return data;
}

export async function cancelChatRun(
	runId: string,
): Promise<ChatRunCancelResponse> {
	const { data, error } = await apiClient.POST(
		"/api/chat/runs/{run_id}/cancel",
		{ params: { path: { run_id: runId } } },
	);
	if (error) {
		throw new Error(getErrorMessage(error, "Failed to stop message"));
	}
	return data;
}
