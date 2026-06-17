/**
 * Manual lossless compaction (§4.3, "Compact older turns").
 *
 * Calls the backend ``POST /api/chat/conversations/{conversation_id}/compact``
 * endpoint, which summarizes older turns into a working-context checkpoint. The
 * stored messages are never modified — the user keeps their full scrollback
 * (§4.1).
 */

import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type CompactConversationResponse =
	components["schemas"]["CompactConversationResponse"];

/** Trigger manual compaction for a conversation. */
export async function compactConversation(
	conversationId: string,
): Promise<CompactConversationResponse> {
	const { data, error } = await apiClient.POST(
		"/api/chat/conversations/{conversation_id}/compact",
		{
			params: { path: { conversation_id: conversationId } },
		},
	);
	if (error) {
		throw new Error(`Compaction failed: ${JSON.stringify(error)}`);
	}
	return data as CompactConversationResponse;
}
