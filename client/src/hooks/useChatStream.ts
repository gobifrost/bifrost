/**
 * Chat WebSocket Streaming Hook
 *
 * Provides real-time streaming chat via the shared WebSocketService.
 * Uses the chat store for state management.
 */

import { useCallback, useRef, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useChatStore } from "@/stores/chatStore";
import {
	webSocketService,
	type ChatStreamChunk,
	type ChatAgentSwitch,
	type AskUserQuestion,
} from "@/services/websocket";
import { generateMessageId, type UnifiedMessage } from "@/lib/chat-utils";

export interface PendingQuestion {
	questions: AskUserQuestion[];
	requestId: string;
}

export interface UseChatStreamOptions {
	conversationId: string | undefined;
	onError?: (error: string) => void;
	onAgentSwitch?: (agentSwitch: ChatAgentSwitch) => void;
}

export interface UseChatStreamReturn {
	sendMessage: (message: string, conversationIdOverride?: string) => void;
	isConnected: boolean;
	isStreaming: boolean;
	// AskUserQuestion support
	pendingQuestion: PendingQuestion | null;
	answerQuestion: (answers: Record<string, string>) => void;
	// Stop/interrupt support
	stopStreaming: () => void;
}

export function useChatStream({
	conversationId,
	onError,
	onAgentSwitch,
}: UseChatStreamOptions): UseChatStreamReturn {
	const queryClient = useQueryClient();
	const [isConnected, setIsConnected] = useState(() => webSocketService.isConnected());
	const [pendingQuestion, setPendingQuestion] =
		useState<PendingQuestion | null>(null);

	// Track current conversation for closure safety
	const currentConversationIdRef = useRef<string | undefined>(conversationId);

	// Ref for handleChunk to avoid effect dependency issues
	const handleChunkRef = useRef<((chunk: ChatStreamChunk) => void) | null>(
		null,
	);

	const {
		isStreaming,
		startStreaming,
		completeStream,
		setStreamError,
		resetStream,
		addSystemEvent,
		addMessage,
		setTodos,
	} = useChatStore();

	// Update ref when conversationId changes
	useEffect(() => {
		currentConversationIdRef.current = conversationId;
	}, [conversationId]);

	// Handle incoming chat stream chunks
	const handleChunk = useCallback(
		(chunk: ChatStreamChunk) => {
			// Handle title update - refresh conversations to show new title
			if (chunk.type === "title_update") {
				queryClient.invalidateQueries({
					queryKey: ["get", "/api/chat/conversations"],
				});
				if (chunk.conversation_id) {
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/chat/conversations/{conversation_id}",
							{
								params: {
									path: {
										conversation_id: chunk.conversation_id,
									},
								},
							},
						],
					});
				}
				return;
			}

			// Only process chunks for current conversation
			if (
				chunk.conversation_id &&
				chunk.conversation_id !== currentConversationIdRef.current
			) {
				return;
			}

			switch (chunk.type) {
				case "message_start": {
					const convId = currentConversationIdRef.current;
					if (!convId) break;

					// Get local_id from chunk (echoed back from server)
					const localId = chunk.local_id;

					// If we have a localId and user_message_id, update the optimistic message with server ID
					if (localId && chunk.user_message_id) {
						// Map localId to server ID for future dedup
						useChatStore
							.getState()
							.mapLocalIdToServerId(convId, localId, chunk.user_message_id);

						const messages =
							useChatStore.getState().messagesByConversation[convId] || [];
						const optimistic = messages.find(
							(m) =>
								(m as UnifiedMessage).localId === localId &&
								(m as UnifiedMessage).isOptimistic,
						);
						if (optimistic) {
							// Replace optimistic with server-confirmed version
							const confirmed: UnifiedMessage = {
								...(optimistic as UnifiedMessage),
								id: chunk.user_message_id,
								isOptimistic: false,
								localId: localId, // Keep localId for reference
							};
							// Update in store - replace by localId match
							const updated = messages.map((m) =>
								(m as UnifiedMessage).localId === localId &&
								(m as UnifiedMessage).isOptimistic
									? confirmed
									: m,
							);
							useChatStore.getState().setMessages(convId, updated);
						}
					}

					// Create assistant message (with server-provided ID and current timestamp)
					if (chunk.assistant_message_id) {
						const assistantMessage: UnifiedMessage = {
							id: chunk.assistant_message_id,
							conversation_id: convId,
							role: "assistant",
							content: "",
							sequence: Date.now(),
							created_at: new Date().toISOString(),
							isStreaming: true,
							isOptimistic: false, // Not optimistic - we have server ID
						};
						addMessage(convId, assistantMessage);

						// Track which message is streaming
						useChatStore
							.getState()
							.setStreamingMessageIdForConversation(
								convId,
								chunk.assistant_message_id,
							);
					}

					// Invalidate to fetch user message (server-confirmed)
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/chat/conversations/{conversation_id}/messages",
							{
								params: {
									path: { conversation_id: convId },
								},
							},
						],
					});
					break;
				}

				case "delta":
					if (chunk.content) {
						const convId = currentConversationIdRef.current;
						if (!convId) break;

						const streamingId = useChatStore.getState().streamingMessageIds[convId];

						// If no streaming message exists (after assistant_message_end), create new one
						if (!streamingId) {
							const newMessageId = generateMessageId();
							const newAssistantMessage: UnifiedMessage = {
								id: newMessageId,
								conversation_id: convId,
								role: "assistant",
								content: chunk.content,
								sequence: Date.now(),
								created_at: new Date().toISOString(),
								isStreaming: true,
								isOptimistic: false,
							};
							addMessage(convId, newAssistantMessage);
							useChatStore.getState().setStreamingMessageIdForConversation(convId, newMessageId);
						} else {
							// Append to existing streaming message
							const currentMessages = useChatStore.getState().messagesByConversation[convId] || [];
							const currentMsg = currentMessages.find((m) => m.id === streamingId);
							useChatStore.getState().updateMessage(convId, streamingId, {
								content: (currentMsg?.content || "") + chunk.content,
							});
						}
					}
					break;

				case "tool_call":
					if (chunk.tool_call && chunk.message_id) {
						const convId = currentConversationIdRef.current;
						if (convId) {
							// Add TOOL_CALL message directly
							const toolCallMessage: UnifiedMessage = {
								id: chunk.message_id,
								conversation_id: convId,
								role: "tool_call",
								content: null,
								tool_name: chunk.tool_call.name,
								tool_input: chunk.tool_call.arguments,
								tool_state: "running",
								tool_call_id: chunk.tool_call.id,
								execution_id: chunk.execution_id || null,
								sequence: Date.now(),
								created_at: new Date().toISOString(),
							};
							addMessage(convId, toolCallMessage);
						}
					}
					break;

				case "tool_progress":
					// Tool progress events are handled via the tool execution persistence system
					// They update toolExecutionsByConversation directly
					break;

				case "tool_result":
					if (chunk.tool_result && chunk.message_id) {
						const convId = currentConversationIdRef.current;
						if (convId) {
							// Update the TOOL_CALL message with result
							useChatStore.getState().updateMessage(convId, chunk.message_id, {
								tool_state: chunk.tool_result.error ? "error" : "completed",
								tool_result: chunk.tool_result.error
									? { error: chunk.tool_result.error }
									: chunk.tool_result.result,
								duration_ms: chunk.tool_result.duration_ms,
							});
						}
					}
					break;

				case "assistant_message_start":
					// Message segment is starting - nothing to do, message is already being built
					break;

				case "assistant_message_end": {
					// Text segment complete - finalize current message
					// Next delta will create a NEW message
					const convId = currentConversationIdRef.current;
					if (convId) {
						const streamingId = useChatStore.getState().streamingMessageIds[convId];
						if (streamingId) {
							useChatStore.getState().updateMessage(convId, streamingId, {
								isStreaming: false,
								isFinal: true,
							});
							useChatStore.getState().setStreamingMessageIdForConversation(convId, null);
						}
					}
					break;
				}

				case "done": {
					const convId = currentConversationIdRef.current;
					const streamingId = convId
						? useChatStore.getState().streamingMessageIds[convId]
						: null;

					// Mark message as no longer streaming and apply usage metadata
					if (convId && streamingId) {
						useChatStore.getState().updateMessage(convId, streamingId, {
							isStreaming: false,
							isFinal: true,
							token_count_input: chunk.token_count_input ?? undefined,
							token_count_output: chunk.token_count_output ?? undefined,
							duration_ms: chunk.duration_ms ?? undefined,
						});

						// Clear streaming ID
						useChatStore
							.getState()
							.setStreamingMessageIdForConversation(convId, null);
					}

					completeStream();

					// Refresh messages from API - this is the source of truth
					if (convId) {
						queryClient.invalidateQueries({
							queryKey: [
								"get",
								"/api/chat/conversations/{conversation_id}/messages",
								{
									params: {
										path: { conversation_id: convId },
									},
								},
							],
						});
						queryClient.invalidateQueries({
							queryKey: ["get", "/api/chat/conversations"],
						});
					}
					break;
				}

				case "agent_switch": {
					if (chunk.agent_switch) {
						onAgentSwitch?.(chunk.agent_switch);
						const convId = currentConversationIdRef.current;
						if (convId) {
							addSystemEvent(convId, {
								id: `event-${Date.now()}`,
								type: "agent_switch",
								timestamp: new Date().toISOString(),
								agentName: chunk.agent_switch.agent_name,
								agentId: chunk.agent_switch.agent_id,
								reason:
									chunk.agent_switch.reason === "@mention"
										? "@mention"
										: "routed",
							});
						}
					}
					break;
				}

				case "ask_user_question": {
					// SDK is asking user a question - show modal
					if (chunk.questions && chunk.request_id) {
						setPendingQuestion({
							questions: chunk.questions,
							requestId: chunk.request_id,
						});
					}
					break;
				}

				case "todo_update": {
					// SDK is updating the todo list
					if (chunk.todos) {
						setTodos(chunk.todos);
					}
					break;
				}

				case "error": {
					const errorMsg = chunk.error || "Unknown error occurred";
					setStreamError(errorMsg);
					onError?.(errorMsg);

					const convId = currentConversationIdRef.current;
					if (convId) {
						addSystemEvent(convId, {
							id: `error-${Date.now()}`,
							type: "error",
							timestamp: new Date().toISOString(),
							error: errorMsg,
						});
					}

					// Clear any pending question on error
					setPendingQuestion(null);
					resetStream();
					break;
				}
			}
		},
		[
			queryClient,
			completeStream,
			setStreamError,
			resetStream,
			onError,
			onAgentSwitch,
			addSystemEvent,
			addMessage,
			setTodos,
		],
	);

	// Keep handleChunk ref updated for use in effects (avoids dependency issues)
	useEffect(() => {
		handleChunkRef.current = handleChunk;
	}, [handleChunk]);

	// Send message via WebSocket
	const sendMessage = useCallback(
		async (message: string, conversationIdOverride?: string) => {
			const targetConversationId = conversationIdOverride ?? conversationId;
			if (!targetConversationId) {
				toast.error("No conversation selected");
				return;
			}

			// Ensure connected
			if (!webSocketService.isConnected()) {
				await webSocketService.connectToChat(targetConversationId);
			}

			// Generate stable ID for user message
			const userMessageId = generateMessageId();
			const now = new Date().toISOString();

			// Add optimistic user message with stable ID
			const userMessage: UnifiedMessage = {
				id: userMessageId,
				conversation_id: targetConversationId,
				role: "user",
				content: message,
				sequence: Date.now(),
				created_at: now,
				isOptimistic: true,
				localId: userMessageId, // Use same ID as localId for dedup
			};
			addMessage(targetConversationId, userMessage);

			// Start streaming state (no assistant placeholder yet - created on message_start)
			startStreaming();

			// Send the chat message with localId for deduplication
			const sent = webSocketService.sendChatMessage(
				targetConversationId,
				message,
				userMessageId,
			);
			if (!sent) {
				try {
					await webSocketService.connectToChat(targetConversationId);
					webSocketService.sendChatMessage(
						targetConversationId,
						message,
						userMessageId,
					);
				} catch (error) {
					console.error(
						"[useChatStream] Failed to send message:",
						error,
					);
					setStreamError("Failed to send message");
					resetStream();
				}
			}
		},
		[conversationId, addMessage, startStreaming, setStreamError, resetStream],
	);

	// Auto-connect when conversation changes - single subscription path
	useEffect(() => {
		if (!conversationId) return;

		let unsubscribe: (() => void) | null = null;

		// Reset stream state directly from store
		useChatStore.getState().resetStream();

		// Connect and subscribe
		const setup = async () => {
			try {
				await webSocketService.connectToChat(conversationId);
				// Subscribe to chat stream (replaces any existing callback)
				unsubscribe = webSocketService.onChatStream(
					conversationId,
					(chunk) => handleChunkRef.current?.(chunk),
				);
				setIsConnected(true);
			} catch (error) {
				console.error("[useChatStream] Failed to connect:", error);
				setIsConnected(false);
			}
		};
		setup();

		return () => {
			unsubscribe?.();
		};
	}, [conversationId]);

	// Track connection status from service via event subscription
	useEffect(() => {
		return webSocketService.onConnectionStatusChange(setIsConnected);
	}, []);

	// Answer a pending AskUserQuestion
	const answerQuestion = useCallback(
		(answers: Record<string, string>) => {
			if (!conversationId || !pendingQuestion) {
				return;
			}

			webSocketService.sendChatAnswer(
				conversationId,
				pendingQuestion.requestId,
				answers,
			);
			setPendingQuestion(null);
		},
		[conversationId, pendingQuestion],
	);

	// Stop the current streaming operation
	const stopStreaming = useCallback(() => {
		if (!conversationId) {
			return;
		}

		webSocketService.sendChatStop(conversationId);

		// Finalize any in-progress streaming message (same as "done" handler)
		const streamingId =
			useChatStore.getState().streamingMessageIds[conversationId];
		if (streamingId) {
			useChatStore.getState().updateMessage(conversationId, streamingId, {
				isStreaming: false,
				isFinal: true,
			});
			useChatStore
				.getState()
				.setStreamingMessageIdForConversation(conversationId, null);
		}

		setPendingQuestion(null);
		resetStream();
	}, [conversationId, resetStream]);

	return {
		sendMessage,
		isConnected,
		isStreaming,
		pendingQuestion,
		answerQuestion,
		stopStreaming,
	};
}
