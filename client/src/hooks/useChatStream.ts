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
import type { AttachmentPublic } from "@/services/chatAttachments";
import type { ChatModelTierId } from "@/services/chatModels";

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
	sendMessage: (
		message: string,
		conversationIdOverride?: string,
		attachments?: AttachmentPublic[],
		modelTier?: ChatModelTierId,
	) => Promise<void>;
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
	const [isConnected, setIsConnected] = useState(() =>
		webSocketService.isConnected(),
	);
	const [pendingQuestion, setPendingQuestion] =
		useState<PendingQuestion | null>(null);

	// Track current conversation for closure safety
	const currentConversationIdRef = useRef<string | undefined>(conversationId);

	// Ref for handleChunk to avoid effect dependency issues
	const handleChunkRef = useRef<((chunk: ChatStreamChunk) => void) | null>(
		null,
	);
	const artifactJobUnsubscribersRef = useRef(new Map<string, () => void>());
	const runAssistantMessageIdRef = useRef<string | null>(null);

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

	useEffect(
		() => () => {
			artifactJobUnsubscribersRef.current.forEach((unsubscribe) =>
				unsubscribe(),
			);
			artifactJobUnsubscribersRef.current.clear();
		},
		[],
	);

	// Handle incoming chat stream chunks
	const handleChunk = useCallback(
		(chunk: ChatStreamChunk) => {
			const observeArtifactJob = (
				result: unknown,
				messageId: string,
			) => {
				if (!result || typeof result !== "object") return;
				const job = result as {
					type?: string;
					job_id?: string;
					kind?: string;
					conversation_id?: string;
				};
				if (
					job.type !== "platform_job" ||
					job.kind !== "video_generation" ||
					!job.job_id ||
					artifactJobUnsubscribersRef.current.has(job.job_id)
				) {
					return;
				}
				const unsubscribe = webSocketService.onPlatformJobUpdate(
					job.job_id,
					(update) => {
						if (
							update.status !== "succeeded" &&
							update.status !== "failed" &&
							update.status !== "cancelled"
						) {
							return;
						}
						const convId =
							job.conversation_id ||
							currentConversationIdRef.current;
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
						}
						queryClient.invalidateQueries({
							queryKey: ["chat-artifacts"],
						});
						if (update.status === "succeeded") {
							toast.success("Video ready");
						} else {
							toast.error("Video generation did not finish", {
								description:
									update.error?.message ||
									"Open the notification for details.",
							});
						}
						artifactJobUnsubscribersRef.current
							.get(job.job_id!)?.();
						artifactJobUnsubscribersRef.current.delete(job.job_id!);
						if (convId) {
							useChatStore.getState().updateMessage(convId, messageId, {
								tool_state:
									update.status === "succeeded"
										? "completed"
										: "error",
								tool_result: {
									...job,
									status: update.status,
								},
							});
						}
					},
				);
				artifactJobUnsubscribersRef.current.set(job.job_id, unsubscribe);
			};

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
							.mapLocalIdToServerId(
								convId,
								localId,
								chunk.user_message_id,
							);

						const messages =
							useChatStore.getState().messagesByConversation[
								convId
							] || [];
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
							useChatStore
								.getState()
								.setMessages(convId, updated);
						}
					}

					// Create assistant message (with server-provided ID and current timestamp)
					if (chunk.assistant_message_id) {
						runAssistantMessageIdRef.current = chunk.assistant_message_id;
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

						const streamingId =
							useChatStore.getState().streamingMessageIds[convId];

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
							useChatStore
								.getState()
								.setStreamingMessageIdForConversation(
									convId,
									newMessageId,
								);
						} else {
							// Append to existing streaming message
							const currentMessages =
								useChatStore.getState().messagesByConversation[
									convId
								] || [];
							const currentMsg = currentMessages.find(
								(m) => m.id === streamingId,
							);
							useChatStore
								.getState()
								.updateMessage(convId, streamingId, {
									content:
										(currentMsg?.content || "") +
										chunk.content,
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

				case "artifact_ready": {
					const convId = currentConversationIdRef.current;
					const artifact = chunk.artifact;
					if (!convId || !chunk.message_id || !artifact?.id)
						break;
					const messages =
						useChatStore.getState().messagesByConversation[
							convId
						] || [];
					const message = messages.find(
						(item) => item.id === chunk.message_id,
					);
					const attachments = message?.attachments ?? [];
					if (
						!attachments.some((item) => item.id === artifact.id)
					) {
						useChatStore
							.getState()
							.updateMessage(convId, chunk.message_id, {
								attachments: [
									...attachments,
									{
										id: artifact.id,
										filename: artifact.filename,
										content_type: artifact.content_type,
										size_bytes: artifact.size_bytes,
										kind: "artifact",
									},
								],
							});
					}
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/chat/conversations/{conversation_id}/messages",
							{ params: { path: { conversation_id: convId } } },
						],
					});
					break;
				}

				case "artifact_failed":
					toast.error("File generation failed", {
						description:
							chunk.content ||
							"The artifact could not be created.",
					});
					break;

				case "artifact_started":
					break;

				case "tool_progress":
					// Tool progress events are handled via the tool execution persistence system
					// They update toolExecutionsByConversation directly
					break;

				case "tool_result":
					if (chunk.tool_result && chunk.message_id) {
						observeArtifactJob(
							chunk.tool_result.result,
							chunk.message_id,
						);
						const convId = currentConversationIdRef.current;
						if (convId) {
							// Update the TOOL_CALL message with result
							useChatStore
								.getState()
								.updateMessage(convId, chunk.message_id, {
									tool_state: chunk.tool_result.error
										? "error"
										: "completed",
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
						const streamingId =
							useChatStore.getState().streamingMessageIds[convId];
						if (streamingId) {
							useChatStore
								.getState()
								.updateMessage(convId, streamingId, {
									// Intermediate text is persisted under its own ID. The
									// pre-generated run ID is reserved for the final response.
									id: chunk.message_id || streamingId,
									isStreaming: false,
									isFinal: true,
								});
							useChatStore
								.getState()
								.setStreamingMessageIdForConversation(
									convId,
									null,
								);
						}
					}
					break;
				}

				case "done": {
					const convId = currentConversationIdRef.current;
					const streamingId = convId
						? useChatStore.getState().streamingMessageIds[convId]
						: null;

					const summaryMessageId =
						chunk.message_id || runAssistantMessageIdRef.current || streamingId;

					// The final segment may have a temporary client ID when it starts
					// after tool execution. Replace that ID with the persisted summary
					// ID so the visible response cannot remain stuck in streaming state.
					const messageToFinalize = streamingId || summaryMessageId;
					if (convId && messageToFinalize) {
						useChatStore
							.getState()
							.updateMessage(convId, messageToFinalize, {
								...(summaryMessageId && messageToFinalize !== summaryMessageId
									? { id: summaryMessageId }
									: {}),
								isStreaming: false,
								isFinal: true,
								token_count_input:
									chunk.token_count_input ?? undefined,
								token_count_output:
									chunk.token_count_output ?? undefined,
								duration_ms: chunk.duration_ms ?? undefined,
							});

						// Clear streaming ID
						useChatStore
							.getState()
							.setStreamingMessageIdForConversation(convId, null);
					}
					runAssistantMessageIdRef.current = null;

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
		async (
			message: string,
			conversationIdOverride?: string,
			attachments: AttachmentPublic[] = [],
			modelTier: ChatModelTierId = "balanced",
		) => {
			const targetConversationId =
				conversationIdOverride ?? conversationId;
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
				attachments,
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
				attachments.map((attachment) => attachment.id),
				modelTier,
			);
			if (!sent) {
				try {
					await webSocketService.connectToChat(targetConversationId);
					const retried = webSocketService.sendChatMessage(
						targetConversationId,
						message,
						userMessageId,
						attachments.map((attachment) => attachment.id),
						modelTier,
					);
					if (!retried) throw new Error("WebSocket is not connected");
				} catch (error) {
					console.error(
						"[useChatStream] Failed to send message:",
						error,
					);
					setStreamError("Failed to send message");
					resetStream();
					throw error;
				}
			}
		},
		[
			conversationId,
			addMessage,
			startStreaming,
			setStreamError,
			resetStream,
		],
	);

	// Auto-connect when conversation changes - single subscription path
	useEffect(() => {
		if (!conversationId) return;

		let unsubscribe: (() => void) | null = null;

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
