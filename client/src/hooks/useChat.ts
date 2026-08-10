/**
 * Chat API hooks using openapi-react-query pattern
 *
 * Provides hooks for:
 * - Listing agents and conversations
 * - Managing conversations (CRUD)
 * - Fetching messages
 * - Sending messages (non-streaming)
 */

import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { $api, apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import type { components } from "@/lib/v1";
import { useChatStore } from "@/stores/chatStore";

// Re-export types for convenience
export type AgentPublic = components["schemas"]["AgentPublic"];
export type AgentSummary = components["schemas"]["AgentSummary"];
export type ConversationPublic = components["schemas"]["ConversationPublic"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationCreate = components["schemas"]["ConversationCreate"];
export type MessagePublic = components["schemas"]["MessagePublic"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type ChatResponse = components["schemas"]["ChatResponse"];

/** Helper to extract error message from API error response */
function getErrorMessage(error: unknown, fallback: string): string {
	if (typeof error === "object" && error && "message" in error) {
		return String((error as Record<string, unknown>)["message"]);
	}
	if (error instanceof Error) {
		return error.message;
	}
	return fallback;
}

// ==================== API Functions ====================

/** Get all available agents */
export async function getAgents(): Promise<AgentSummary[]> {
	const { data, error } = await apiClient.GET("/api/agents");
	if (error)
		throw new Error(getErrorMessage(error, "Failed to fetch agents"));
	return data || [];
}

/** Get a specific agent by ID */
export async function getAgent(agentId: string): Promise<AgentPublic> {
	const { data, error } = await apiClient.GET("/api/agents/{agent_id}", {
		params: { path: { agent_id: agentId } },
	});
	if (error) throw new Error(getErrorMessage(error, "Failed to fetch agent"));
	return data!;
}

/** Get all conversations for the current user */
export async function getConversations(): Promise<ConversationSummary[]> {
	const { data, error } = await apiClient.GET("/api/chat/conversations");
	if (error)
		throw new Error(
			getErrorMessage(error, "Failed to fetch conversations"),
		);
	return data || [];
}

/** Get a specific conversation by ID */
export async function getConversation(
	conversationId: string,
): Promise<ConversationPublic> {
	const { data, error } = await apiClient.GET(
		"/api/chat/conversations/{conversation_id}",
		{
			params: { path: { conversation_id: conversationId } },
		},
	);
	if (error)
		throw new Error(getErrorMessage(error, "Failed to fetch conversation"));
	return data!;
}

/** Create a new conversation */
export async function createConversation(
	request: ConversationCreate,
): Promise<ConversationPublic> {
	const { data, error } = await apiClient.POST("/api/chat/conversations", {
		body: request,
	});
	if (error)
		throw new Error(
			getErrorMessage(error, "Failed to create conversation"),
		);
	return data!;
}

/** Delete a conversation */
export async function deleteConversation(
	conversationId: string,
): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/chat/conversations/{conversation_id}",
		{
			params: { path: { conversation_id: conversationId } },
		},
	);
	if (error)
		throw new Error(
			getErrorMessage(error, "Failed to delete conversation"),
		);
}

/** Get messages for a conversation */
export async function getMessages(
	conversationId: string,
): Promise<MessagePublic[]> {
	const { data, error } = await apiClient.GET(
		"/api/chat/conversations/{conversation_id}/messages",
		{
			params: { path: { conversation_id: conversationId } },
		},
	);
	if (error)
		throw new Error(getErrorMessage(error, "Failed to fetch messages"));
	return data || [];
}

/** Send a message (non-streaming) */
export async function sendMessage(
	conversationId: string,
	message: string,
): Promise<ChatResponse> {
	const { data, error } = await apiClient.POST(
		"/api/chat/conversations/{conversation_id}/messages",
		{
			params: { path: { conversation_id: conversationId } },
			body: { message, stream: false },
		},
	);
	if (error)
		throw new Error(getErrorMessage(error, "Failed to send message"));
	return data!;
}

// ==================== Query Hooks ====================

/** Hook to fetch all agents */
export function useAgents() {
	return $api.useQuery("get", "/api/agents", {});
}

/** Hook to fetch a specific agent */
export function useAgent(agentId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/agents/{agent_id}",
		{ params: { path: { agent_id: agentId ?? "" } } },
		{ enabled: !!agentId },
	);
}

/** Hook to fetch all conversations */
export function useConversations() {
	return $api.useQuery("get", "/api/chat/conversations", {});
}

/** Hook to fetch a specific conversation */
export function useConversation(conversationId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/chat/conversations/{conversation_id}",
		{ params: { path: { conversation_id: conversationId ?? "" } } },
		{ enabled: !!conversationId },
	);
}

/** Hook to fetch messages for a conversation */
export function useMessages(conversationId: string | undefined) {
	return $api.useQuery(
		"get",
		"/api/chat/conversations/{conversation_id}/messages",
		{ params: { path: { conversation_id: conversationId ?? "" } } },
		{ enabled: !!conversationId },
	);
}

// ==================== Mutation Hooks ====================

/** Hook to create a new conversation */
export function useCreateConversation() {
	const queryClient = useQueryClient();
	const { addConversation, setActiveConversation } = useChatStore();

	return $api.useMutation("post", "/api/chat/conversations", {
		onSuccess: (data) => {
			// Add to local state
			const summary: ConversationSummary = {
				id: data.id,
				agent_id: data.agent_id,
				agent_name: data.agent_name ?? null,
				title: data.title ?? null,
				updated_at: data.updated_at ?? new Date().toISOString(),
				last_message_preview: null,
			};
			addConversation(summary);
			setActiveConversation(data.id);

			// Invalidate conversations query
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/chat/conversations"],
			});
		},
		onError: (error) => {
			toast.error("Failed to start conversation", {
				description: getErrorMessage(error, "Unknown error"),
			});
		},
	});
}

/** Hook to delete a conversation */
export function useDeleteConversation() {
	const queryClient = useQueryClient();
	const removeConversation = useChatStore(
		(state) => state.removeConversation,
	);

	return $api.useMutation(
		"delete",
		"/api/chat/conversations/{conversation_id}",
		{
			onSuccess: (_, variables) => {
				const conversationId = (
					variables.params as { path: { conversation_id: string } }
				).path.conversation_id;

				// Remove from local state
				removeConversation(conversationId);

				// Invalidate queries
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/chat/conversations"],
				});

				toast.success("Conversation deleted");
			},
			onError: (error) => {
				toast.error("Failed to delete conversation", {
					description: getErrorMessage(error, "Unknown error"),
				});
			},
		},
	);
}

/** Hook to send a message (non-streaming) */
export function useSendMessage() {
	const queryClient = useQueryClient();
	const { addMessage, activeConversationId } = useChatStore();

	return $api.useMutation(
		"post",
		"/api/chat/conversations/{conversation_id}/messages",
		{
			onMutate: async (variables) => {
				const conversationId = (
					variables.params as { path: { conversation_id: string } }
				).path.conversation_id;
				const message = (variables.body as { message: string }).message;

				// Optimistically add user message
				const userMessage: MessagePublic = {
					id: `temp-${Date.now()}`,
					conversation_id: conversationId,
					role: "user",
					content: message,
					sequence: Date.now(),
					created_at: new Date().toISOString(),
				};
				addMessage(conversationId, userMessage);
			},
			onSuccess: (response, variables) => {
				const conversationId = (
					variables.params as { path: { conversation_id: string } }
				).path.conversation_id;

				// Add assistant response
				const assistantMessage: MessagePublic = {
					id: response.message_id,
					conversation_id: conversationId,
					role: "assistant",
					content: response.content,
					tool_calls: response.tool_calls ?? undefined,
					token_count_input: response.token_count_input ?? undefined,
					token_count_output:
						response.token_count_output ?? undefined,
					duration_ms: response.duration_ms ?? undefined,
					sequence: Date.now() + 1,
					created_at: new Date().toISOString(),
				};
				addMessage(conversationId, assistantMessage);

				// Refresh messages to get accurate data
				queryClient.invalidateQueries({
					queryKey: [
						"get",
						"/api/chat/conversations/{conversation_id}/messages",
						{
							params: {
								path: { conversation_id: conversationId },
							},
						},
					],
				});
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/chat/conversations"],
				});
			},
			onError: (error) => {
				// Remove optimistic user message on error
				if (activeConversationId) {
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/chat/conversations/{conversation_id}/messages",
							{
								params: {
									path: {
										conversation_id: activeConversationId,
									},
								},
							},
						],
					});
				}

				toast.error("Failed to send message", {
					description: getErrorMessage(error, "Unknown error"),
				});
			},
		},
	);
}

// ==================== Stats Hooks ====================

/** Pricing rates per million tokens (USD) */
const PRICING_RATES: Record<string, { input: number; output: number }> = {
	// Anthropic models
	"claude-opus-4": { input: 15.0, output: 75.0 },
	"claude-sonnet-4": { input: 3.0, output: 15.0 },
	"claude-3-5-sonnet": { input: 3.0, output: 15.0 },
	"claude-3-opus": { input: 15.0, output: 75.0 },
	"claude-3-sonnet": { input: 3.0, output: 15.0 },
	"claude-3-haiku": { input: 0.25, output: 1.25 },
	// OpenAI models
	"gpt-4o": { input: 2.5, output: 10.0 },
	"gpt-4o-mini": { input: 0.15, output: 0.6 },
	"gpt-4-turbo": { input: 10.0, output: 30.0 },
	"gpt-4": { input: 30.0, output: 60.0 },
	"gpt-3.5-turbo": { input: 0.5, output: 1.5 },
};

/** Get pricing for a model (matches by prefix) */
function getPricing(
	model: string | null | undefined,
): { input: number; output: number } | null {
	if (!model) return null;
	const normalizedModel = model.toLowerCase();
	for (const [key, rates] of Object.entries(PRICING_RATES)) {
		if (normalizedModel.includes(key.toLowerCase())) {
			return rates;
		}
	}
	return null;
}

/** Conversation statistics including token counts and estimated cost */
export interface ConversationStats {
	model: string | null;
	totalInputTokens: number;
	totalOutputTokens: number;
	totalTokens: number;
	estimatedCostUsd: number | null;
}

/** Hook to compute conversation statistics from messages */
export function useConversationStats(
	conversationId: string | undefined,
): ConversationStats | null {
	const { data: messages } = useMessages(conversationId);

	return useMemo(() => {
		if (!messages || messages.length === 0) return null;

		const assistantMessages = messages.filter(
			(m) => m.role === "assistant",
		);

		// Find the model from the first message that has one
		const model = assistantMessages.find((m) => m.model)?.model ?? null;

		// Sum up token counts
		const totalInputTokens = assistantMessages.reduce(
			(sum, m) => sum + (m.token_count_input || 0),
			0,
		);
		const totalOutputTokens = assistantMessages.reduce(
			(sum, m) => sum + (m.token_count_output || 0),
			0,
		);
		const totalTokens = totalInputTokens + totalOutputTokens;

		// Calculate estimated cost
		let estimatedCostUsd: number | null = null;
		const pricing = getPricing(model);
		if (pricing && totalTokens > 0) {
			const inputCost = (totalInputTokens / 1_000_000) * pricing.input;
			const outputCost = (totalOutputTokens / 1_000_000) * pricing.output;
			estimatedCostUsd = inputCost + outputCost;
		}

		return {
			model,
			totalInputTokens,
			totalOutputTokens,
			totalTokens,
			estimatedCostUsd,
		};
	}, [messages]);
}
